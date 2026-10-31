"""Restart recovery uses exclusive ownership, never job age or guessed success."""
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, contextmanager
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from choplab import api, core, jobs, metadata, store


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "_DATABASE_URL", "")
    monkeypatch.setattr(jobs, "_active", set())
    monkeypatch.setattr(jobs, "_cancel", set())
    monkeypatch.setattr(jobs, "_volume_id", None)
    monkeypatch.setattr(jobs, "recovery_error", None)
    return tmp_path


@pytest.fixture(params=["sqlite", "postgres"])
def database(library, request, monkeypatch):
    store.initialize()
    if request.param == "postgres":
        real_url = os.environ.get("CHOPLAB_TEST_POSTGRES_URL")
        if real_url:
            # Never accept a production target for these destructive fixture resets.
            target = urlsplit(real_url)
            assert os.environ.get("CHOPLAB_DISPOSABLE_POSTGRES_TEST") == "yes"
            assert target.hostname == "postgres" and target.path == "/choplab_recovery_test"
            assert target.username == "choplab_test" and target.password == "synthetic-ci-password"
            monkeypatch.setattr(store, "_DATABASE_URL", real_url)
            monkeypatch.setattr(store, "_initialized", False)
            with store._postgres_conn() as con:
                store._postgres_schema(con)
                con.execute("DELETE FROM docs")
                assert con.connection.pgconn.ssl_in_use
                assert con.connection.info.get_parameters()["sslmode"] == "verify-full"
            store.initialize()
            return request.param
        # Exercise the PostgreSQL connection/ownership branch with the same fixed
        # SQL against a local DB; no cloud credentials or services in unit tests.
        @contextmanager
        def connection():
            with closing(sqlite3.connect(library / "choplab.sqlite3")) as con:
                yield con
                con.commit()
        monkeypatch.setattr(store, "_DATABASE_URL", "postgresql://unit-test/library")
        monkeypatch.setattr(store, "_initialized", True)
        monkeypatch.setattr(store, "_postgres_conn", connection)
    return request.param


def seed(job_type, status="running", *, id=None, target_id=None, created=1, owner=None):
    id = id or f"job-{job_type}"
    target_id = target_id or f"target-{job_type}"
    params = {"export_id": target_id} if job_type == "export" else {"source_id": target_id}
    job = store.put("job", {"id": id, "project_id": "p", "job_type": job_type,
                           "params": params, "status": status, "created_at": created,
                           "owner_volume": owner, "progress": 0.3, "result": None}, parent="p")
    target = jobs._target(job_type, params)
    kind, _, state, pointer, error = target
    doc = store.put(kind, {"id": target_id, "project_id": "p", state: "queued",
                           pointer: id, error: None, "analysis": {"bpm": 92},
                           "stems": {"vocals": "saved.wav"}, "files": [{"name": "saved.wav"}]},
                    parent="p")
    return job, doc


def identify_volume():
    with jobs.runtime():
        assert jobs.recovery_error is None
        return jobs._volume_id


@pytest.mark.parametrize("job_type", ["import", "analyze", "separate", "export"])
@pytest.mark.parametrize("state", ["queued", "running"])
def test_restart_repairs_linked_pending_records_and_preserves_data(database, job_type, state):
    identity = identify_volume()
    job, doc = seed(job_type, state, owner=identity)
    kind, id, status, _, error = jobs._target(job_type, job["params"])
    if state == "running":
        store.update(kind, id, **{status: "running" if job_type in ("separate", "export") else "analyzing"})
    store.put("sample", {"id": "saved-sample", "favorite": True}, parent=doc["id"])
    with jobs.runtime():
        recovered = store.get("job", job["id"])
        assert recovered["status"] == "interrupted"
        assert recovered["current_stage"] == "interrupted" and recovered["completed_at"]
        assert recovered["result"] is None and recovered["progress"] == 0.3
        linked = store.get(kind, doc["id"])
        assert linked[status] == "interrupted"
        assert "Retry" in linked[error] and "automatically retried" in recovered["error_message"]
        for field in ("analysis", "stems", "files"):
            assert linked[field] == doc[field]
        assert store.get("sample", "saved-sample")["favorite"]
        assert len(store.list_("job")) == 1
    with jobs.runtime():
        assert store.get("job", job["id"]) == recovered  # idempotent


def test_recovery_requires_lock_and_never_interrupts_local_workers(library):
    with pytest.raises(store.StoreConfigurationError, match="exclusive"):
        jobs.recover_interrupted()
    with jobs.runtime():
        jobs._active.add("live")
        try:
            with pytest.raises(jobs.Busy, match="active"):
                jobs.recover_interrupted()
        finally:
            jobs._active.clear()


def test_lock_contention_from_another_process_is_rejected(library):
    env = {**os.environ, "CHOPLAB_DATA": str(library)}
    code = "from choplab import jobs\nwith jobs.runtime():\n print('unexpected')"
    with jobs.runtime():
        contender = subprocess.run([sys.executable, "-c", code], env=env,
                                   capture_output=True, text=True, timeout=30)
        assert contender.returncode != 0
        assert "Another ChopLab runtime owns this audio volume" in contender.stderr
    with jobs.runtime():
        assert not jobs.recovery_error


def test_abrupt_process_restart_recovers_import_separation_and_export(library):
    env = {**os.environ, "CHOPLAB_DATA": str(library)}
    code = """
import json, threading, time
from choplab import jobs, store
jobs._compute = threading.BoundedSemaphore(1)
def work(ctx, **params):
    ctx.progress('working', 0.3)
    time.sleep(120)
for kind in ('import', 'separate', 'export'):
    jobs._handlers[kind] = (work, 'default')
with jobs.runtime():
    ids = []
    for kind in ('import', 'separate', 'export'):
        target = 'target-' + kind
        params = {'export_id': target} if kind == 'export' else {'source_id': target}
        record_kind, _, state, _, _ = jobs._target(kind, params)
        store.put(record_kind, {'id': target, state: 'queued', 'stems': {'saved': 'saved.wav'}}, parent='p')
        ids.append(jobs.submit(kind, 'p', params)['id'])
    print(json.dumps(ids), flush=True)
    time.sleep(120)
"""
    process = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
        reader.start()
        stdout = ready.get(timeout=30)
        assert stdout, "Worker never announced successful startup"
        ids = json.loads(stdout.strip())
        process.kill()
        process.communicate(timeout=10)
        assert any(store.get("job", id)["status"] == "queued" for id in ids)
        with jobs.runtime():
            assert all(store.get("job", id)["status"] == "interrupted" for id in ids)
            for kind in ("import", "separate", "export"):
                target = jobs._target(kind, {"export_id": "target-export"} if kind == "export" else {"source_id": f"target-{kind}"})
                doc = store.get(target[0], target[1])
                assert doc[target[2]] == "interrupted" and doc["stems"] == {"saved": "saved.wav"}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_graceful_shutdown_keeps_lock_until_worker_finishes(library, monkeypatch):
    started, finish, drained = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def work(ctx):
        started.set()
        assert finish.wait(10)
        return {}
    monkeypatch.setitem(jobs._handlers, "drain-test", (work, "default"))
    runtime = jobs.runtime()
    runtime.__enter__()
    job = jobs.submit("drain-test", "p", {})
    assert started.wait(5)
    def close_runtime():
        try:
            runtime.__exit__(None, None, None)
        except Exception as e:
            errors.append(e)
        finally:
            drained.set()
    closer = threading.Thread(target=close_runtime)
    closer.start()
    try:
        assert not drained.wait(0.05)
        env = {**os.environ, "CHOPLAB_DATA": str(library)}
        contender = subprocess.run([sys.executable, "-c", "from choplab import jobs\nwith jobs.runtime(): pass"],
                                   env=env, capture_output=True, text=True, timeout=30)
        assert contender.returncode != 0 and "Another ChopLab runtime" in contender.stderr
        assert store.get("job", job["id"])["status"] == "running"
    finally:
        finish.set()
        closer.join(timeout=10)
    assert drained.is_set() and not errors
    assert store.get("job", job["id"])["status"] == "completed"


@pytest.mark.parametrize("owner", [None, "foreign-owner"])
def test_foreign_postgres_jobs_are_untouched_with_actionable_api_and_offline_recovery(database, owner):
    if database != "postgres":
        pytest.skip("PostgreSQL-only unsafe ownership condition")
    identify_volume()
    job, doc = seed("analyze", owner=owner)
    with TestClient(api.app) as client:
        res = client.get("/api/system/recovery")
        assert res.status_code == 409 and res.json()["read_only"]
        assert jobs.RECOVERY_COMMAND in res.json()["detail"]
        assert store.get("job", job["id"]) == job
        assert store.get("source", doc["id"]) == doc
        assert client.get("/api/system/capabilities").json()["job_recovery_error"]
        assert client.post("/api/jobs/" + job["id"] + "/retry").status_code == 503
        assert client.get("/api/health").status_code == 200
    with jobs.runtime(offline_recovery=True):
        assert store.get("job", job["id"])["status"] == "interrupted"
        assert store.get("job", job["id"])["owner_volume"] == owner
        assert store.get("source", doc["id"])["analysis_status"] == "interrupted"
    with jobs.runtime():
        assert jobs.recovery_error is None


def test_postgres_volume_binding_is_not_overwritten(database, library, monkeypatch):
    if database != "postgres":
        pytest.skip("PostgreSQL-only volume binding")
    original = identify_volume()
    with pytest.raises(store.StoreConfigurationError, match="another audio volume"):
        store.bind_job_volume("different-volume")
    assert store.get("runtime", "job_runtime_volume")["volume_id"] == original
    # A genuinely empty replacement volume must fail even with an empty queue.
    assert store.list_("job") == []
    fresh = library / "fresh-volume"
    fresh.mkdir()
    assert list(fresh.iterdir()) == []
    monkeypatch.setattr(core, "DATA_DIR", fresh)
    monkeypatch.setattr(store, "_initialized", False)
    store.initialize()
    with jobs.runtime():
        assert "another audio volume" in jobs.recovery_error
        assert store.list_("job") == []
        assert store.get("runtime", "job_runtime_volume")["volume_id"] == original
    with pytest.raises(store.StoreConfigurationError, match="another audio volume"):
        with jobs.runtime(offline_recovery=True):
            pytest.fail("Offline confirmation must not bypass audio ownership")


def test_real_postgres_full_runtime_uses_verified_tls(database):
    if database != "postgres" or not os.environ.get("CHOPLAB_TEST_POSTGRES_URL"):
        pytest.skip("Requires explicitly isolated CI PostgreSQL")
    with jobs.runtime():
        assert jobs.recovery_error is None
        with store._postgres_conn() as con:
            assert con.connection.pgconn.ssl_in_use
            assert con.connection.info.get_parameters()["sslmode"] == "verify-full"


@pytest.mark.parametrize("job_type", ["analyze", "separate", "export"])
def test_completed_targets_and_newer_results_are_not_overwritten(database, job_type):
    identity = identify_volume()
    old, _ = seed(job_type, owner=identity)
    newer, doc = seed(job_type, "completed", id="newer", created=2, owner=identity)
    kind, id, status, _, _ = jobs._target(job_type, newer["params"])
    done = store.update(kind, id, **{status: "analyzed" if job_type == "analyze" else "done"})
    with jobs.runtime():
        assert store.get("job", old["id"])["status"] == "interrupted"
        assert store.get("job", newer["id"]) == newer
        assert store.get(kind, id) == done
        with pytest.raises(jobs.Busy, match="Newer work"):
            jobs.retry(old["id"])
        assert store.get(kind, id) == done


def test_legacy_orphan_and_partial_recovery_records_are_repaired(library):
    _, source = seed("import", "interrupted")
    store.put("export", {"id": "orphan", "status": "queued", "files": []})
    with jobs.runtime():
        assert store.get("source", source["id"])["analysis_status"] == "interrupted"
        assert store.get("export", "orphan")["status"] == "interrupted"


def test_retry_claims_target_clears_error_and_rejects_parallel_source_work(library, monkeypatch):
    class Pool:
        def submit(self, fn, *args):
            self.work = fn, args
    pool = Pool()
    monkeypatch.setattr(jobs, "_pools", {"default": pool})
    monkeypatch.setattr(jobs, "_pending", threading.BoundedSemaphore(2))
    monkeypatch.setitem(jobs._handlers, "analyze", (lambda ctx, **params: {}, "default"))
    old, source = seed("analyze", "interrupted")
    with jobs.runtime():
        retry = jobs.retry(old["id"])
        pending = store.get("source", source["id"])
        assert pending["analysis_job_id"] == retry["id"]
        assert pending["analysis_status"] == "queued" and pending["error"] is None
        try:
            with pytest.raises(jobs.Busy, match="Newer"):
                jobs.retry(old["id"])
            with pytest.raises(jobs.Busy, match="already"):
                jobs.submit("separate", "p", {"source_id": source["id"], "model_id": "builtin-hpss"})
        finally:
            jobs.cancel(retry["id"])
            fn, args = pool.work
            fn(*args)
        assert store.get("source", source["id"])["analysis_status"] == "canceled"


def test_operator_command_requires_explicit_confirmation(library, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["metadata", "recover-jobs"])
    with pytest.raises(SystemExit) as e:
        metadata.main()
    assert e.value.code == 2


def test_operator_command_recovers_only_pending_work(database, monkeypatch, capsys):
    identify_volume()
    pending, source = seed("analyze", owner="stopped-legacy-runtime")
    completed = store.put("job", {"id": "saved-history", "status": "completed", "result": {"ok": True}})
    monkeypatch.setattr(sys, "argv", ["metadata", "recover-jobs", "--confirm-all-runtimes-stopped"])
    metadata.main()
    assert "no retries submitted" in capsys.readouterr().out
    assert store.get("job", pending["id"])["status"] == "interrupted"
    assert store.get("job", pending["id"])["owner_volume"] == "stopped-legacy-runtime"
    assert store.get("source", source["id"])["analysis_status"] == "interrupted"
    assert store.get("job", completed["id"]) == completed


def test_hosted_auth_remains_outside_recovery_boundary():
    middleware = [m.cls.__name__ for m in api.app.user_middleware]
    assert middleware.index("OwnerAccess") < middleware.index("BaseHTTPMiddleware")


def test_older_finisher_does_not_change_new_owner_status(library):
    old, _ = seed("analyze", "failed")
    _, source = seed("analyze", "queued", id="newer", created=2)
    jobs._finish_target(old, "failed", "Old failure")
    assert store.get("source", source["id"]) == source


def test_executor_rejection_fails_target_and_releases_capacity(library, monkeypatch):
    class RejectedPool:
        def submit(self, *args):
            raise RuntimeError("Executor stopped")
    monkeypatch.setattr(jobs, "_pools", {"default": RejectedPool()})
    monkeypatch.setattr(jobs, "_pending", threading.BoundedSemaphore(1))
    monkeypatch.setitem(jobs._handlers, "analyze", (lambda ctx, **params: {}, "default"))
    store.put("source", {"id": "s", "analysis_status": "analyzed", "analysis": {"bpm": 92}})
    with pytest.raises(RuntimeError, match="Executor"):
        jobs.submit("analyze", "p", {"source_id": "s"})
    source = store.get("source", "s")
    assert source["analysis_status"] == "failed" and source["analysis"] == {"bpm": 92}
    assert jobs._pending.acquire(blocking=False) and not jobs._active
    jobs._pending.release()


def test_latest_retry_is_allowed_even_when_timestamps_tie(library, monkeypatch):
    old, _ = seed("analyze", "interrupted")
    latest, _ = seed("analyze", "interrupted", id="latest", created=old["created_at"])
    monkeypatch.setattr(jobs, "_submit", lambda *args: {"retried": args[0]})
    assert jobs.retry(latest["id"]) == {"retried": "analyze"}
    with pytest.raises(jobs.Busy, match="Newer"):
        jobs.retry(old["id"])


def test_retry_cannot_overwrite_newer_separation_sample_discovery(library, monkeypatch):
    old, _ = seed("analyze", "interrupted")
    separate, _ = seed("separate", "completed", target_id="target-analyze", created=2)
    store.update("source", "target-analyze", analysis_job_id=old["id"])
    monkeypatch.setattr(jobs, "_submit", lambda *args: pytest.fail("Do not dispatch a stale retry"))
    with pytest.raises(jobs.Busy, match="Newer"):
        jobs.retry(old["id"])
    assert store.get("job", separate["id"])["status"] == "completed"

def test_retry_analysis_without_decoded_audio_reimports_original(library, monkeypatch):
    source = store.put("source", {"id": "missing-decoded", "project_id": "p", "internal_audio_path": None})
    called = []
    monkeypatch.setattr(jobs, "submit", lambda *args: called.append(args) or {"status": "queued"})
    with TestClient(api.app) as client:
        assert client.post(f"/api/sources/{source['id']}/analyze").status_code == 202
    assert called == [("import", "p", {"source_id": source["id"]})]
