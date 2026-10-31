"""Real isolated PostgreSQL + TLS S3 acceptance; never uses production targets."""
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import numpy as np
from fastapi.testclient import TestClient

from choplab import api, audio_io, core, drums, durable, jobs, schemas, store
from choplab.object_storage import ObjectRef, S3ByteStorage, S3Settings, StorageError


@pytest.fixture
def remote(tmp_path, monkeypatch):
    url = os.environ.get("CHOPLAB_TEST_POSTGRES_URL")
    cert = os.environ.get("CHOPLAB_TEST_S3_CA")
    if not url or not cert:
        pytest.skip("Requires disposable TLS PostgreSQL and S3 acceptance services")
    assert os.environ.get("CHOPLAB_DISPOSABLE_POSTGRES_TEST") == "yes"
    target = urlsplit(url)
    assert target.hostname == "postgres" and target.path == "/choplab_recovery_test"
    assert target.username == "choplab_test" and target.password == "synthetic-ci-password"
    root = tmp_path / "audio"
    root.mkdir()
    for module in (core, api, schemas):
        monkeypatch.setattr(module, "DATA_DIR", root)
    monkeypatch.setattr(store, "_DATABASE_URL", url)
    monkeypatch.setattr(store, "_initialized", True)
    settings = S3Settings("https://s3:9000", "synthetic-durable", "us-east-1",
                          "synthetic-access", "synthetic-secret", prefix="acceptance",
                          ca_bundle=cert, max_bytes=16 << 20)
    monkeypatch.setattr(durable, "_settings", settings)
    monkeypatch.setattr(durable, "_owner", None)
    monkeypatch.setattr(durable, "_fence", None)
    with store._conn() as con:
        store._postgres_schema(con)
        durable._schema(con)
        con.execute("DELETE FROM docs")
        con.execute("DELETE FROM choplab_asset_links")
        con.execute("DELETE FROM choplab_assets")
        con.execute("DELETE FROM choplab_runtime")
    adapter = S3ByteStorage(settings, root)
    try:
        adapter.client.create_bucket(Bucket=settings.bucket)
    except adapter._client_error as e:
        assert e.response["Error"]["Code"] in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists")
    config = tmp_path / "storage.json"
    config.write_text(json.dumps(asdict(settings)))
    monkeypatch.setenv("CHOPLAB_STORAGE_CONFIG_FILE", str(config))
    return root, settings, config


def write(root, relative, data=b"identical-audio-bytes"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def raw(kind, id):
    with store._conn() as con:
        return json.loads(con.execute("SELECT doc FROM docs WHERE kind=? AND id=?", (kind, id)).fetchone()[0])


def replace_volume(remote, monkeypatch, *, destroy_old=False):
    root, _, _ = remote
    successor = root.parent / "replacement"
    successor.mkdir()
    for module in (core, api, schemas):
        monkeypatch.setattr(module, "DATA_DIR", successor)
    if destroy_old:
        shutil.rmtree(root)
        assert not root.exists()
    return successor


def test_all_asset_fields_restore_after_volume_replacement(remote, monkeypatch):
    root, _, _ = remote
    with jobs.runtime():
        source = store.put("source", {
            "id": "source", "project_id": "p",
            "original_file_path": write(root, "projects/p/sources/source/original.wav"),
            "internal_audio_path": write(root, "projects/p/sources/source/audio.wav"),
            "features_path": write(root, "projects/p/sources/source/features.npz"),
            "stems": {"vocals": write(root, "cache/stems/vocals.wav")},
            "analysis": {"bpm": 92}, "analysis_status": "analyzed",
        })
        store.put("variation", {"id": "variation", "preview_path": write(root, "projects/p/previews/v.wav")})
        store.put("preview", {"id": "studio", "preview_path": write(root, "projects/p/previews/studio.wav")})
        sound = write(root, "projects/p/sounds/custom.wav")
        store.put("sound", {"id": "sound", "sample_path": sound})
        store.put("project", {"id": "p", "source_ids": ["source"], "arrangement": {"bass": {"sample_path": sound}}})
        store.put("kit", {"id": "kit-trap-kick", "audio_path": write(root, "kits/trap/custom/kick.wav")})
        store.put("export", {"id": "export", "status": "done", "folder": str(root / "projects/p/exports/e"),
                             "files": [{"name": "mix.wav", "path": write(root, "projects/p/exports/e/mix.wav")}]})
        saved = raw("source", "source")
        assert str(root) not in json.dumps(saved)
        assert len(saved["_assets"]) == 4
    successor = replace_volume(remote, monkeypatch, destroy_old=True)
    with jobs.runtime():
        assert jobs.recovery_error is None
        restored = store.get("source", "source")
        for field in ("original_file_path", "internal_audio_path", "features_path"):
            assert Path(restored[field]).is_relative_to(successor)
            assert durable.ensure_path(restored[field], restored).read_bytes() == b"identical-audio-bytes"
        for kind, id in (("variation", "variation"), ("preview", "studio"), ("project", "p"),
                         ("sound", "sound"), ("export", "export"), ("kit", "kit-trap-kick")):
            assert store.get(kind, id)
        assert drums.kit_path("trap", "kick").exists()
        assert restored["analysis"] == source["analysis"]


def test_shared_references_retirement_and_non_reusable_keys(remote):
    root, _, _ = remote
    with jobs.runtime():
        first = store.put("sound", {"id": "first", "sample_path": write(root, "sounds/a.wav")})
        shared = {**first, "id": "shared"}
        store.put("sound", shared)
        asset = next(iter(first["_assets"].values()))
        store.delete("sound", "first")
        durable.reconcile()
        assert durable.materialize(asset).exists()
        store.delete("sound", "shared")
        with pytest.raises(StorageError, match="Retired"):
            store.put("sound", {**first, "id": "illegal-new-reference"})
        successor = store.put("sound", {"id": "successor", "sample_path": write(root, "sounds/b.wav")})
        second = next(iter(successor["_assets"].values()))
        with store._conn() as con:
            old_key = json.loads(durable._asset(con, asset)[3])["key"]
            new_key = json.loads(durable._asset(con, second)[3])["key"]
        assert old_key != new_key
        durable.reconcile()
        assert durable.materialize(second).read_bytes() == b"identical-audio-bytes"


def test_same_path_new_output_is_published_not_overwritten_by_old_cache_hydration(remote):
    root, _, _ = remote
    with jobs.runtime():
        file = write(root, "features.npz", b"old-feature-data")
        first = store.put("source", {"id": "analysis", "features_path": file})
        Path(file).write_bytes(b"new-feature-data")
        second = store.update("source", "analysis", analysis={"bpm": 100})
        assert first["_assets"] != second["_assets"]
        assert Path(second["features_path"]).read_bytes() == b"new-feature-data"
        assert store.get("source", "analysis")["analysis"] == {"bpm": 100}
        durable.reconcile()
        assert Path(second["features_path"]).read_bytes() == b"new-feature-data"


def test_metadata_read_during_feature_write_cannot_restore_over_pending_output(remote, monkeypatch):
    root, _, _ = remote
    with jobs.runtime():
        store.put("source", {"id": "analysis", "project_id": "p",
                            "features_path": write(root, "projects/p/sources/analysis/features.npz", b"old-data")})
        original = np.savez
        def concurrent_reader(path, **features):
            original(path, **features)
            store.get("source", "analysis")
        monkeypatch.setattr(np, "savez", concurrent_reader)
        monkeypatch.setattr(api.analysis, "analyze", lambda *args: ({"bpm": 100, "key": "C"}, {"energy": [1, 2]}))
        monkeypatch.setattr(api, "_discover", lambda *args: 0)
        api._analyze(jobs.JobContext("new-analysis"), "analysis", np.zeros((2, 2), dtype=np.float32))
        result = store.get("source", "analysis")
        assert Path(result["features_path"]).name == "features-new-analysis.npz"
        with np.load(result["features_path"]) as features:
            np.testing.assert_array_equal(features["energy"], [1, 2])


def test_offline_snapshot_restore_preserves_live_assets_and_tombstones(remote, monkeypatch):
    root, _, _ = remote
    with jobs.runtime():
        store.put("sound", {"id": "live", "sample_path": write(root, "live.wav")})
        store.put("sound", {"id": "retired", "sample_path": write(root, "retired.wav")})
        store.delete("sound", "retired")
        durable.reconcile()
        snapshot = store.export_snapshot()
        old_fence = durable._fence
    replace_volume(remote, monkeypatch, destroy_old=True)
    with store._conn() as con:
        con.execute("DELETE FROM docs")
        con.execute("DELETE FROM choplab_asset_links")
        con.execute("DELETE FROM choplab_assets")
    store.restore_snapshot(snapshot)
    with jobs.runtime():
        assert durable._fence > old_fence
        saved = store.get("sound", "live")
        assert durable.ensure_path(saved["sample_path"], saved).read_bytes() == b"identical-audio-bytes"
        with store._conn() as con:
            assert con.execute("SELECT COUNT(*) FROM choplab_assets WHERE state='deleted'").fetchone()[0] == 1


@pytest.mark.parametrize("owner", [None, "foreign-volume"])
def test_legacy_jobs_remain_untouched_with_actionable_read_only_api(remote, owner):
    with jobs.runtime():
        doc = {"id": "legacy", "status": "running", "job_type": "analyze",
               "params": {"source_id": "legacy-source"}}
        if owner:
            doc["owner_volume"] = owner
        store.put("job", doc)
        store.put("source", {"id": "legacy-source", "analysis_status": "analyzing"})
    with TestClient(api.app) as client:
        response = client.get("/api/system/recovery")
        assert response.status_code == 409
        assert "left untouched" in response.json()["detail"]
        assert "matching original volume" in response.json()["detail"]
        assert "recover-jobs" in client.get("/api/system/capabilities").json()["job_recovery_error"]
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/projects", json={"name": "blocked"}).status_code == 503
        assert raw("job", "legacy")["status"] == "running"
        assert raw("source", "legacy-source")["analysis_status"] == "analyzing"


def test_abrupt_durable_restart_recovers_linked_statuses_without_changing_newer_results(remote, monkeypatch):
    root, _, _ = remote
    signal = root.parent / "crash-ready"
    code = """
import os,time
from pathlib import Path
from choplab import durable,jobs,store
durable.configure()
with jobs.runtime():
    store.put('source',{'id':'source','analysis_status':'analyzing','separation_status':'running'})
    store.put('export',{'id':'export','status':'queued'})
    store.put('source',{'id':'newer','analysis_status':'analyzed','analysis':{'bpm':101},'separation_status':'done'})
    store.put('export',{'id':'saved','status':'done','note':'completed history'})
    for kind,params in [('analyze',{'source_id':'source'}),('separate',{'source_id':'source','model_id':'builtin-hpss'}),('export',{'export_id':'export','project_id':'p','request':{}})]:
        store.put('job',{'id':'job-'+kind,'job_type':kind,'status':'running','owner_volume':'durable','params':params})
    Path(os.environ['SIGNAL']).write_text('ready')
    while True: time.sleep(.1)
"""
    process = subprocess.Popen([sys.executable, "-c", code], env={**worker_env(remote, root), "SIGNAL": str(signal)},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_file(signal, process)
        process.kill()
        process.communicate(timeout=10)
        with store._conn() as con:
            con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
        replace_volume(remote, monkeypatch, destroy_old=True)
        with TestClient(api.app) as client:
            assert client.get("/api/system/recovery").json()["ok"]
            for id in ("analyze", "separate", "export"):
                assert raw("job", "job-" + id)["status"] == "interrupted"
                assert "retry" in raw("job", "job-" + id)["error_message"].lower()
            assert raw("source", "source")["analysis_status"] == "interrupted"
            assert raw("source", "source")["separation_status"] == "interrupted"
            assert raw("export", "export")["status"] == "interrupted"
            assert raw("source", "newer")["analysis"] == {"bpm": 101}
            assert raw("source", "newer")["analysis_status"] == "analyzed"
            assert raw("export", "saved")["status"] == "done"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_failed_publication_never_commits_success_and_orphans_reconcile(remote, monkeypatch):
    root, _, _ = remote
    with jobs.runtime():
        original = S3ByteStorage.publish
        def ambiguous(self, relative):
            original(self, relative)
            raise StorageError("Object storage publish failed")
        monkeypatch.setattr(S3ByteStorage, "publish", ambiguous)
        with pytest.raises(StorageError, match="quota.*SlowDown"):
            store.put("export", {"id": "failed", "status": "done", "files": [{"path": write(root, "exports/a.wav")}]})
        assert store.get("export", "failed") is None
        with store._conn() as con:
            assert con.execute("SELECT state FROM choplab_assets").fetchone()[0] == "uploading"
    monkeypatch.setattr(S3ByteStorage, "publish", original)
    with jobs.runtime():
        with store._conn() as con:
            assert con.execute("SELECT state FROM choplab_assets").fetchone()[0] == "deleted"


def test_authenticated_range_download_restores_missing_cache(remote, monkeypatch):
    root, _, _ = remote
    data = b"RIFFsynthetic-range-audio"
    with jobs.runtime():
        store.put("source", {"id": "range", "internal_audio_path": write(root, "source/range.wav", data)})
    replace_volume(remote, monkeypatch, destroy_old=True)
    with TestClient(api.app) as client:
        response = client.get("/api/sources/range/audio", headers={"Range": "bytes=4-8"})
        assert response.status_code == 206 and response.content == data[4:9]
        assert response.headers["content-range"] == f"bytes 4-8/{len(data)}"
        assert client.get("/api/health").status_code == 200


def worker_env(remote, root):
    _, _, config = remote
    return {**os.environ, "CHOPLAB_DATA": str(root), "CHOPLAB_STORAGE_CONFIG_FILE": str(config),
            "CHOPLAB_DATABASE_URL": os.environ["CHOPLAB_TEST_POSTGRES_URL"], "DATABASE_URL": ""}


WORKER = """
import json, os, time
from pathlib import Path
from choplab import durable, jobs, store
from choplab.object_storage import S3ByteStorage
root = Path(os.environ['CHOPLAB_DATA']); root.mkdir(exist_ok=True)
signal = Path(os.environ['SIGNAL_DIR'])
durable.configure()
original_delete = S3ByteStorage.delete
original_publish = S3ByteStorage.publish
def delayed_delete(self, ref):
    (signal/'delete-ready').write_text(ref.key)
    while not (signal/'release').exists(): time.sleep(.02)
    original_delete(self, ref)
def delayed_publish(self, relative):
    (signal/'upload-ready').write_text('reserved before upload')
    while not (signal/'release').exists(): time.sleep(.02)
    return original_publish(self, relative)
try:
    with jobs.runtime():
        path = root/'asset.wav'; path.write_bytes(b'identical-audio-bytes')
        store.put('sound', {'id':'old','sample_path':str(path)})
        if os.environ['ACTION'] == 'delete':
            store.delete('sound','old')
            S3ByteStorage.delete = delayed_delete
            durable.reconcile()
        elif os.environ['ACTION'] == 'upload':
            S3ByteStorage.publish = delayed_publish
            store.put('sound', {'id':'old-upload','sample_path':str(path)})
        else:
            (signal/'write-ready').write_text('ready')
            while not (signal/'release').exists(): time.sleep(.02)
            store.update('sound','old', note='stale mutation')
except durable.OwnershipLost:
    (signal/'fenced').write_text('fenced')
"""


def wait_file(path, process):
    deadline = time.monotonic() + 20
    while not path.exists() and time.monotonic() < deadline:
        assert process.poll() is None, process.communicate(timeout=2)
        time.sleep(0.02)
    assert path.exists(), "Worker never reached the required race boundary"


@pytest.mark.parametrize("action", ["write", "delete", "upload"])
def test_expired_worker_is_fenced_and_delayed_delete_cannot_remove_successor(remote, monkeypatch, action):
    root, _, _ = remote
    signal = root.parent / "signals"
    signal.mkdir()
    env = {**worker_env(remote, root), "SIGNAL_DIR": str(signal), "ACTION": action}
    worker = subprocess.Popen([sys.executable, "-c", WORKER], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_file(signal / f"{action}-ready", worker)
        # The worker deliberately remains alive beyond expiry; this is not a PID/age assumption.
        with store._conn() as con:
            con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
        successor = replace_volume(remote, monkeypatch)
        with jobs.runtime():
            new = store.put("sound", {"id": "new", "sample_path": write(successor, "asset.wav")})
            (signal / "release").write_text("go")
            out, err = worker.communicate(timeout=20)
            assert worker.returncode == 0, (out, err)
            assert (signal / "fenced").exists()
            assert durable.materialize(next(iter(new["_assets"].values()))).read_bytes() == b"identical-audio-bytes"
            if action == "write":
                assert "note" not in store.get("sound", "old")
            elif action == "delete":
                assert store.get("sound", "old") is None
            else:
                assert store.get("sound", "old-upload") is None
                durable.reconcile()
                assert durable.materialize(next(iter(new["_assets"].values()))).read_bytes() == b"identical-audio-bytes"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=10)


def test_live_overlap_refuses_takeover_and_metadata_snapshot_contains_asset_catalog(remote):
    root, _, _ = remote
    with jobs.runtime():
        store.put("sound", {"id": "saved", "sample_path": write(root, "a.wav")})
        env = worker_env(remote, root.parent / "other")
        process = subprocess.run([sys.executable, "-c", "from choplab import durable,jobs; durable.configure()\nwith jobs.runtime():\n assert not durable._ready\n print('health-ready read-only follower')"],
                                 env=env, capture_output=True, text=True, timeout=20)
        assert process.returncode == 0 and "read-only follower" in process.stdout
        snapshot = store.export_snapshot()
        assert snapshot["durable_audio"]["assets"] and snapshot["durable_audio"]["links"]
        durable.validate_snapshot(snapshot["durable_audio"], [json.loads(row[5]) for row in snapshot["rows"]])
        same_cache = subprocess.run(
            [sys.executable, "-c", "from choplab import durable,jobs; durable.configure()\nwith jobs.runtime(): pass"],
            env=worker_env(remote, root), capture_output=True, text=True, timeout=20)
        assert same_cache.returncode != 0
        assert "Another process owns this local audio cache" in same_cache.stderr


@pytest.mark.parametrize("reconcile_failure", [False, True])
def test_health_ready_read_only_overlap_automatically_takes_over_after_expiry(remote, reconcile_failure):
    root, _, _ = remote
    follower = root.parent / "follower"
    signals = root.parent / "follower-signals"
    signals.mkdir()
    code = """
import os,time
from pathlib import Path
from fastapi.testclient import TestClient
from choplab import api,durable
signal=Path(os.environ['SIGNAL_DIR'])
if os.environ['RECONCILE_FAILURE'] == 'yes':
    def fail(): raise durable.StorageError('Synthetic storage throttle 503 SlowDown')
    durable.reconcile=fail
with TestClient(api.app) as client:
    assert client.get('/api/health').status_code == 200
    assert client.post('/api/projects',json={'name':'blocked'}).status_code == 503
    (signal/'ready').write_text('health-ready and read-only')
    until=time.monotonic()+20
    while not durable._ready and not durable._runtime_error and time.monotonic()<until: time.sleep(.05)
    if os.environ['RECONCILE_FAILURE'] == 'yes':
        status=client.get('/api/system/recovery')
        assert status.status_code == 409
        assert '503 SlowDown' in status.json()['detail'] and 'restart' in status.json()['detail']
        assert '503 SlowDown' in client.get('/api/system/capabilities').json()['job_recovery_error']
        assert client.post('/api/projects',json={'name':'still-blocked'}).status_code == 503
        (signal/'acquired').write_text('explicit reconciliation failure')
    else:
        assert durable._ready
        result=client.post('/api/projects',json={'name':'successor'})
        assert result.status_code==201, result.text
        (signal/'acquired').write_text(result.json()['id'])
"""
    with jobs.runtime():
        worker = subprocess.Popen([sys.executable, "-c", code], env={**worker_env(remote, follower), "SIGNAL_DIR": str(signals),
                                  "RECONCILE_FAILURE": "yes" if reconcile_failure else "no"},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            wait_file(signals / "ready", worker)
            with store._conn() as con:
                con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
            wait_file(signals / "acquired", worker)
            out, err = worker.communicate(timeout=15)
            assert worker.returncode == 0, (out, err)
            if not reconcile_failure:
                assert store.get("project", (signals / "acquired").read_text())["name"] == "successor"
            with pytest.raises(durable.OwnershipLost):
                store.put("project", {"id": "stale", "name": "must not commit"})
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=10)


def test_studio_audio_workflow_survives_replacement_with_custom_sounds(remote, monkeypatch):
    if os.environ.get("CHOPLAB_FULL_DURABLE_AUDIO") != "yes":
        pytest.skip("Full DSP acceptance runs separately from the small capped race tests")
    root, _, _ = remote
    monkeypatch.setattr(api, "MIN_FREE", 0)
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 300)
    with TestClient(api.app) as client:
        project = client.post("/api/projects", json={"name": "/durable-audio"}).json()["id"]
        imported = client.post(f"/api/projects/{project}/demo").json()
        job = jobs.wait(imported["job"]["id"], timeout=300)
        assert job["status"] == "completed", job.get("error_message")
        source = imported["source"]["id"]
        separated = client.post(f"/api/sources/{source}/separate", json={"model_id": "builtin-hpss"}).json()
        assert jobs.wait(separated["id"], timeout=300)["status"] == "completed"
        # Custom sounds enter the same durable catalog as source/render output.
        sound = root / "one-shot.wav"
        tone = np.sin(2 * np.pi * 65.4 * np.arange(4800) / 48000).astype(np.float32) * 0.2
        audio_io.write_wav(sound, np.stack([tone, tone], axis=1))
        wav = sound.read_bytes()
        kit = client.post("/api/kits/trap/kick", files={"file": ("kick.wav", wav, "audio/wav")})
        assert kit.status_code == 201, kit.text
        bass = client.post(f"/api/projects/{project}/bass-sample", files={"file": ("808.wav", wav, "audio/wav")})
        assert bass.status_code == 201, bass.text
        generated = client.post(f"/api/projects/{project}/generate", json={"variations": 1, "bars": 4}).json()
        finished = jobs.wait(generated["id"], timeout=300)
        assert finished["status"] == "completed", finished.get("error_message")
        variation = finished["result"]["variation_ids"][0]
        opened = client.post(f"/api/variations/{variation}/open-in-studio")
        assert opened.status_code == 200, opened.text
        arr = opened.json()["arrangement"]
        # Reference the uploaded sound in the saved arrangement without changing render quality.
        arr["bass"]["sample_path"] = bass.json()["sample_path"]
        arr["bass"]["mode"] = "sample"
        assert client.put(f"/api/projects/{project}/arrangement", json=arr).status_code == 200
        preview = client.post(f"/api/projects/{project}/preview", json={"scope": "loop", "loop_bars": 1})
        assert preview.status_code == 200, preview.text
        exported = client.post(f"/api/projects/{project}/export", json={"filename": "durable", "project_json": True}).json()
        finished = jobs.wait(exported["job"]["id"], timeout=300)
        assert finished["status"] == "completed", finished.get("error_message")
        export = exported["export_id"]
        export_doc = store.get("export", export)
        project_file = next(item for item in export_doc["files"] if item["name"].endswith(".json"))
        portable = json.loads(Path(project_file["path"]).read_text())
        assert portable["project"]["name"] == "/durable-audio"
        assert str(root) not in json.dumps(portable)
        assert portable["durable_assets"]
        assert portable["sources"][0]["internal_audio_path"].startswith("asset-path:")
        original_audio = client.get(f"/api/sources/{source}/audio").content
        original_mix = client.get(f"/api/exports/{export}/download").content
        original_preview = client.get(preview.json()["url"]).content
        analysis = client.get(f"/api/sources/{source}/analysis").json()
    replace_volume(remote, monkeypatch, destroy_old=True)
    with TestClient(api.app) as client:
        assert client.get(f"/api/sources/{source}/audio").content == original_audio
        assert client.get(f"/api/exports/{export}/download").content == original_mix
        assert client.get(f"/api/sources/{source}/analysis").json() == analysis
        assert client.get(f"/api/variations/{variation}/audio").status_code == 200
        assert client.get(preview.json()["url"]).status_code == 200
        assert client.get("/api/kits/trap/kick/audio").status_code == 200
        rendered = client.post(f"/api/projects/{project}/preview", json={"scope": "loop", "loop_bars": 1})
        assert rendered.status_code == 200, rendered.text
        assert client.get(rendered.json()["url"]).content == original_preview
        assert client.get(f"/api/projects/{project}").json()["arrangement"]["tracks"] == store.get("project", project)["arrangement"]["tracks"]
        reanalyze = client.post(f"/api/sources/{source}/analyze").json()
        finished = jobs.wait(reanalyze["id"], timeout=300)
        assert finished["status"] == "completed", finished.get("error_message")
