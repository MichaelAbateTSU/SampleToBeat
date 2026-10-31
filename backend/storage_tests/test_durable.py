"""Real isolated PostgreSQL + TLS S3 acceptance; never uses production targets."""
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from choplab import api, core, drums, durable, jobs, schemas, store
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


def replace_volume(remote, monkeypatch):
    root, _, _ = remote
    successor = root.parent / "replacement"
    successor.mkdir()
    for module in (core, api, schemas):
        monkeypatch.setattr(module, "DATA_DIR", successor)
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
    successor = replace_volume(remote, monkeypatch)
    with jobs.runtime():
        assert jobs.recovery_error is None
        restored = store.get("source", "source")
        for field in ("original_file_path", "internal_audio_path", "features_path"):
            assert Path(restored[field]).is_relative_to(successor)
            assert Path(restored[field]).read_bytes() == b"identical-audio-bytes"
        for kind, id in (("variation", "variation"), ("preview", "studio"), ("project", "p"),
                         ("sound", "sound"), ("export", "export"), ("kit", "kit-trap-kick")):
            assert store.get(kind, id)
        assert core.data_path("kits", "trap", "custom", "kick.wav").exists()
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
    replace_volume(remote, monkeypatch)
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
def delayed_delete(self, ref):
    (signal/'delete-ready').write_text(ref.key)
    while not (signal/'release').exists(): time.sleep(.02)
    original_delete(self, ref)
try:
    with jobs.runtime():
        path = root/'asset.wav'; path.write_bytes(b'identical-audio-bytes')
        store.put('sound', {'id':'old','sample_path':str(path)})
        if os.environ['ACTION'] == 'delete':
            store.delete('sound','old')
            S3ByteStorage.delete = delayed_delete
            durable.reconcile()
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


@pytest.mark.parametrize("action", ["write", "delete"])
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
            else:
                assert store.get("sound", "old") is None
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=10)


def test_live_overlap_refuses_takeover_and_metadata_snapshot_contains_asset_catalog(remote):
    root, _, _ = remote
    with jobs.runtime():
        store.put("sound", {"id": "saved", "sample_path": write(root, "a.wav")})
        env = worker_env(remote, root.parent / "other")
        process = subprocess.run([sys.executable, "-c", "from choplab import durable,jobs; durable.configure()\nwith jobs.runtime(): pass"],
                                 env=env, capture_output=True, text=True, timeout=20)
        assert process.returncode != 0 and "Another instance still owns" in process.stderr
        snapshot = store.export_snapshot()
        assert snapshot["durable_audio"]["assets"] and snapshot["durable_audio"]["links"]
        durable.validate_snapshot(snapshot["durable_audio"], [json.loads(row[5]) for row in snapshot["rows"]])
