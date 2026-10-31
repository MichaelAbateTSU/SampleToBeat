from pathlib import Path

import pytest

from choplab import core, durable, store
from choplab.object_storage import StorageError


def test_metadata_decoding_never_materializes(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(durable, "materialize", lambda _: pytest.fail("Metadata downloaded audio"))
    doc = {"id": "source", "internal_audio_path": "asset-path:sources/audio.wav",
           "stems": {"harmonic": "asset-path:stems/h.wav"},
           "folder": "asset-folder:exports", "_assets": {"sources/audio.wav": "a", "stems/h.wav": "h"}}
    decoded = durable.restore(doc)
    assert decoded["internal_audio_path"] == str(tmp_path / "sources/audio.wav")
    assert decoded["stems"]["harmonic"] == str(tmp_path / "stems/h.wav")
    assert decoded["folder"] == str(tmp_path / "exports")
    assert decoded["_assets"] == doc["_assets"]
    assert not list(tmp_path.iterdir())


def test_local_consumers_keep_original_path_behavior(monkeypatch, tmp_path):
    monkeypatch.setattr(durable, "enabled", lambda: False)
    path = tmp_path / "audio.wav"
    assert durable.ensure_path(str(path), {}) == path
    assert not path.exists()


def test_private_path_guard_survives_metadata_only_restore(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    with pytest.raises(StorageError, match="escapes"):
        durable.restore({"sample_path": "asset-path:../outside.wav"})


def test_document_selects_only_its_matching_asset(monkeypatch, tmp_path):
    from contextlib import nullcontext
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(durable, "enabled", lambda: True)
    monkeypatch.setattr(store, "_conn", lambda: nullcontext(None))
    monkeypatch.setattr(durable, "_asset", lambda con, id: ("audio.wav", None, "live", None))
    seen = []
    monkeypatch.setattr(durable, "materialize", lambda id: seen.append(id) or tmp_path / "audio.wav")
    doc = {"_assets": {"audio.wav": "wanted", "stems/unused.wav": "unused"}}
    assert durable.ensure_path(tmp_path / "audio.wav", doc) == tmp_path / "audio.wav"
    assert seen == ["wanted"]
    with pytest.raises(StorageError, match="no durable reference"):
        durable.ensure_path(tmp_path / "missing.wav", doc)
    assert seen == ["wanted"]
