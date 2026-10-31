from collections import namedtuple
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from choplab import api, capacity

Usage = namedtuple("Usage", "total used free")


def test_distinct_exact_values_and_no_path_leak(monkeypatch, tmp_path):
    data, temp = tmp_path / "data", tmp_path / "tmp"
    data.mkdir(); temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(capacity.shutil, "disk_usage",
                        lambda p: Usage(9, 1, 123456789 if Path(p) == data else 987654321))
    monkeypatch.setattr(capacity.os, "stat", lambda p: type("S", (), {"st_dev": 1 if Path(p) == data else 2})())
    result = capacity.storage_capacity(data)
    assert result == {"data_free_bytes": 123456789, "data_free_error": None,
                      "temp_free_bytes": 987654321, "temp_free_error": None,
                      "data_temp_same_filesystem": False}
    assert str(tmp_path) not in str(result)


def test_same_path_matches_real_filesystem(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    result = capacity.storage_capacity(tmp_path)
    assert result["data_free_bytes"] == result["temp_free_bytes"] >= 0
    assert result["data_temp_same_filesystem"] is True


def test_explicit_errors_do_not_expose_details(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def broken(path):
        raise OSError("secret " + str(path))

    monkeypatch.setattr(capacity.shutil, "disk_usage", broken)
    result = capacity.storage_capacity(tmp_path)
    assert result["data_free_bytes"] is None and result["temp_free_bytes"] is None
    assert result["data_free_error"] == result["temp_free_error"] == "unavailable"
    assert result["data_temp_same_filesystem"] is None
    assert "secret" not in str(result)


def test_negative_values_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(1, 1, -1))
    assert capacity.storage_capacity(tmp_path)["data_free_error"] == "invalid"


def test_capabilities_retains_existing_fields_and_adds_exact_values():
    body = TestClient(api.app).get("/api/system/capabilities").json()
    for key in ("hosted", "max_upload_mb", "max_audio_seconds", "disk_free_gb", "data_dir"):
        assert key in body
    assert isinstance(body["data_free_bytes"], int) and body["data_free_bytes"] >= 0
    assert isinstance(body["temp_free_bytes"], int) and body["temp_free_bytes"] >= 0
