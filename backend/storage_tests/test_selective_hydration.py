"""Cold-cache object traffic against the guarded real PostgreSQL/S3 fixture."""
import json
from pathlib import Path

import pytest
import numpy as np
from fastapi.testclient import TestClient

from test_durable import remote, replace_volume, write
from choplab import api, audio_io, durable, jobs, sampling, separation, store
from choplab.object_storage import S3ByteStorage, StorageError


@pytest.fixture
def traffic(monkeypatch):
    counts = {"gets": 0, "bytes": 0}
    original = S3ByteStorage.__init__
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        get = self.client.get_object
        def measured(**params):
            counts["gets"] += 1
            result = get(**params)
            read = result["Body"].read
            def measured_read(*args, **kwargs):
                chunk = read(*args, **kwargs)
                counts["bytes"] += len(chunk)
                return chunk
            result["Body"].read = measured_read
            return result
        self.client.get_object = measured
    monkeypatch.setattr(S3ByteStorage, "__init__", init)
    return counts


def library(remote):
    root, _, _ = remote
    sizes = {"original": 2 << 20, "internal": 3 << 20,
             "features": 1 << 20, "harmonic": 4 << 20, "percussive": 5 << 20}
    with jobs.runtime():
        project = api.create_project(api.schemas.ProjectCreate(name="selective-synthetic"))
        source = store.put("source", {
            "id": "src-selective", "project_id": project["id"], "content_hash": "synthetic",
            "original_file_path": write(root, "source/original.wav", b"o" * sizes["original"]),
            "internal_audio_path": write(root, "source/internal.wav", b"i" * sizes["internal"]),
            "features_path": write(root, "source/features.npz", b"f" * sizes["features"]),
            "stems": {name: write(root, f"source/{name}.wav", name[0].encode() * sizes[name])
                      for name in ("harmonic", "percussive")},
            "analysis_status": "analyzed", "analysis": {"bpm": 92}, "stem_model": "builtin-hpss",
            "duration_seconds": 1200, "peaks": [0.1],
        }, parent=project["id"])
        store.update("project", project["id"], source_ids=[source["id"]])
        store.put("variation", {"id": "var-selective", "project_id": project["id"],
                                "preview_path": write(root, "previews/variation.wav", b"v" * 100)})
        store.put("preview", {"id": "studio_r_123456789abc", "project_id": project["id"],
                              "preview_path": write(root, "previews/studio.wav", b"s" * 200)})
        store.put("export", {"id": "ex-selective", "status": "done", "project_id": project["id"],
                             "files": [{"name": "mix.wav", "path": write(root, "exports/mix.wav", b"m" * 300)},
                                       {"name": "project.json", "path": write(root, "exports/project.json", b"j" * 400)}]})
        store.put("kit", {"id": "kit-trap-kick", "audio_path": write(root, "kits/trap/custom/kick.wav", b"k" * 500)})
    return project["id"], source["id"], sizes


def test_cold_metadata_zero_gets_and_selective_exact_bytes(remote, monkeypatch, traffic):
    p, s, sizes = library(remote)
    successor = replace_volume(remote, monkeypatch, destroy_old=True)
    with TestClient(api.app) as client:
        for path in (f"/api/projects/{p}", f"/api/sources/{s}",
                     f"/api/sources/{s}/analysis", f"/api/sources/{s}/waveform",
                     f"/api/sources/{s}/stems", f"/api/projects/{p}/samples",
                     "/api/kits", "/api/variations/var-selective", "/api/exports/ex-selective"):
            assert client.get(path).status_code == 200
        snapshot = store.export_snapshot()
        assert snapshot["durable_audio"]["assets"]
        assert traffic == {"gets": 0, "bytes": 0}
        assert not list(successor.rglob("*.wav"))
        metadata = dict(traffic)

        audio = client.get(f"/api/sources/{s}/audio", headers={"Range": "bytes=0-63"})
        assert audio.status_code == 206 and audio.content == b"i" * 64
        assert traffic == {"gets": 1, "bytes": sizes["internal"]}
        assert client.get(f"/api/sources/{s}/audio").content == b"i" * sizes["internal"]
        assert client.get(f"/api/projects/{p}").status_code == 200
        assert traffic == {"gets": 1, "bytes": sizes["internal"]}
        assert not (successor / "source/original.wav").exists()
        assert not (successor / "source/harmonic.wav").exists()

        source = store.get("source", s)
        paths, _ = sampling.resolve(source, ["harmonic"])
        assert Path(paths[0]).read_bytes() == b"h" * sizes["harmonic"]
        assert traffic == {"gets": 2, "bytes": sizes["internal"] + sizes["harmonic"]}
        sampling.resolve(source, ["harmonic"])
        assert traffic["gets"] == 2
        assert not (successor / "source/percussive.wav").exists()
        assert not (successor / "source/features.npz").exists()
        for path, expected in (
            ("/api/variations/var-selective/audio", b"v" * 100),
            (f"/api/projects/{p}/preview/studio_r_123456789abc.wav", b"s" * 200),
            ("/api/exports/ex-selective/download?name=mix.wav", b"m" * 300),
        ):
            assert client.get(path).content == expected
        assert traffic["gets"] == 5
        assert not (successor / "exports/project.json").exists()
        (successor / "source/internal.wav").write_bytes(b"corrupt-cache")
        assert client.get(f"/api/sources/{s}/audio").content == b"i" * sizes["internal"]
        assert traffic["gets"] == 6
        print("COLD_HYDRATION_TRAFFIC " + json.dumps({
            "metadata": metadata, "source_cold_range": {"gets": 1, "bytes": sizes["internal"]},
            "all_source_assets_bytes": sum(sizes.values()), "final": traffic,
            "scope": "Actual synthetic MiB-scale objects; not a 20-minute DSP/egress measurement."}))


@pytest.mark.parametrize("failure", ["provider", "integrity"])
def test_failed_selective_download_never_serves_local_or_partial_success(remote, monkeypatch, traffic, failure):
    _, s, _ = library(remote)
    successor = replace_volume(remote, monkeypatch, destroy_old=True)
    original = S3ByteStorage.materialize
    def broken(self, ref, relative):
        if failure == "provider":
            raise StorageError("Synthetic storage throttle 503 SlowDown")
        # Corrupt the object's content while preserving SHA metadata, exercising
        # the adapter's real body verification rather than a mocked hash guard.
        self.client.put_object(Bucket=self.settings.bucket, Key=ref.key,
                               Body=b"wrong", Metadata={"sha256": ref.sha256})
        return original(self, ref, relative)
    monkeypatch.setattr(S3ByteStorage, "materialize", broken)
    with TestClient(api.app) as client:
        assert client.get(f"/api/sources/{s}").status_code == 200
        assert traffic["gets"] == 0
        result = client.get(f"/api/sources/{s}/audio")
        assert result.status_code == 503
        assert "no incomplete audio was served" in result.json()["detail"]
        assert not (successor / "source/internal.wav").exists()
        assert not list(successor.rglob("*.part"))


def test_missing_or_ambiguous_reference_cannot_use_unverified_local_bytes(remote):
    root, _, _ = remote
    with jobs.runtime():
        path = write(root, "unverified.wav", b"local-only")
        with pytest.raises(StorageError, match="missing"):
            durable.ensure_path(path)
        with pytest.raises(StorageError, match="no durable reference"):
            durable.ensure_path(path, {"_assets": {}})
        first = store.put("sound", {"id": "one", "sample_path": path})
        with pytest.raises(StorageError, match="disagrees"):
            durable.ensure_path(write(root, "another.wav"), {
                "_assets": {"another.wav": next(iter(first["_assets"].values()))}})
        Path(path).write_bytes(b"new-bytes")
        store.put("sound", {"id": "two", "sample_path": path})
        with pytest.raises(StorageError, match="ambiguous"):
            durable.ensure_path(path)


def test_discovery_restores_features_and_cached_separation_only_its_stems(remote, monkeypatch, traffic):
    _, s, sizes = library(remote)
    root, _, _ = remote
    with jobs.runtime():
        features = root / "source/features.npz"
        np.savez(features, energy=[1, 2])
        store.update("source", s)
    successor = replace_volume(remote, monkeypatch, destroy_old=True)
    with jobs.runtime():
        def discover(source, analysis, features, **kwargs):
            np.testing.assert_array_equal(features["energy"], [1, 2])
            assert not Path(source["internal_audio_path"]).exists()
            return []
        monkeypatch.setattr(sampling, "discover", discover)
        assert api._discover(jobs.JobContext("discovery-only"), s) == 0
        assert traffic["gets"] == 1
        assert not (successor / "source/original.wav").exists()
        stems = separation.separate(store.get("source", s), "builtin-hpss", lambda *args: None,
                                    working_id="cached-separation")
        assert all(Path(path).exists() for path in stems.values())
        assert traffic["gets"] == 3
        assert traffic["bytes"] == (successor / "source/features.npz").stat().st_size + sizes["harmonic"] + sizes["percussive"]
        assert not (successor / "source/internal.wav").exists()


def test_cold_reanalysis_restores_decoded_input_not_original_or_old_features(remote, monkeypatch, traffic):
    root, _, _ = remote
    with jobs.runtime():
        p = api.create_project(api.schemas.ProjectCreate(name="reanalysis-synthetic"))["id"]
        internal = audio_io.write_wav(root / "internal.wav", np.zeros((480, 2), dtype=np.float32))
        store.put("source", {"id": "reanalyze", "project_id": p, "internal_audio_path": str(internal),
                             "original_file_path": write(root, "original.wav", b"unused-original"),
                             "features_path": write(root, "features.npz", b"unused-features"),
                             "stems": {"harmonic": write(root, "harmonic.wav", b"unused-stem")}})
    successor = replace_volume(remote, monkeypatch, destroy_old=True)
    with jobs.runtime():
        monkeypatch.setattr(api.analysis, "analyze", lambda y, *args: ({"bpm": 100}, {"energy": [1, 2]}))
        monkeypatch.setattr(api, "_discover", lambda *args: 0)
        api._analyze(jobs.JobContext("cold-reanalysis"), "reanalyze")
        assert traffic == {"gets": 1, "bytes": (successor / "internal.wav").stat().st_size}
        assert not (successor / "original.wav").exists()
        assert not (successor / "features.npz").exists()
        assert not (successor / "harmonic.wav").exists()
