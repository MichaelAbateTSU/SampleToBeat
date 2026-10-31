import json

import pytest
from fastapi.testclient import TestClient

from choplab import api, core, store


@pytest.fixture
def empty_store(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "_DATABASE_URL", "")
    return tmp_path


def test_sqlite_snapshot_roundtrip_and_nonempty_restore_guard(empty_store):
    doc = store.put("project", {"id": "snapshot-project", "name": "quotes ' and Unicode \u266b"})
    snapshot = store.export_snapshot()
    assert snapshot["metadata_store"] == "sqlite"
    with pytest.raises(store.StoreConfigurationError, match="not empty"):
        store.restore_snapshot(snapshot)
    store.delete("project", doc["id"])
    store.restore_snapshot(snapshot)
    assert store.get("project", doc["id"]) == doc


def test_snapshot_validation_is_atomic(empty_store):
    snapshot = {"format": "choplab-metadata-1", "rows": [
        ["valid", "project", None, 1.0, 2.0, '{"id":"valid"}'],
        ["mismatch", "project", None, 1.0, 2.0, '{"id":"other"}'],
    ]}
    with pytest.raises(store.StoreConfigurationError, match="ID"):
        store.restore_snapshot(snapshot)
    assert store.list_("project") == []
    snapshot["rows"][1] = ["valid", "project", None, 1.0, 2.0, '{"id":"valid"}']
    with pytest.raises(store.StoreConfigurationError, match="fields"):
        store.restore_snapshot(snapshot)
    assert store.list_("project") == []


def test_postgres_marker_prevents_silent_sqlite_switch(empty_store):
    (empty_store / "metadata-postgres.json").write_text(json.dumps({"postgres_identity": "test-only"}))
    with pytest.raises(store.StoreConfigurationError, match="PostgreSQL"):
        store.initialize()
    with pytest.raises(store.StoreConfigurationError, match="fallback"):
        store.get("project", "anything")


def test_database_url_configuration_is_explicit_and_redacted(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CHOPLAB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CHOPLAB_DATABASE_URL_FILE", raising=False)
    assert store._configured_database() == ""
    secret = tmp_path / "database_url"
    secret.write_text("postgresql://owner:not-a-real-secret@database.example/neondb?sslmode=require")
    monkeypatch.setenv("CHOPLAB_DATABASE_URL_FILE", str(secret))
    assert store._configured_database().startswith("postgresql://")
    monkeypatch.setenv("DATABASE_URL", "conflicting")
    with pytest.raises(store.StoreConfigurationError, match="not both"):
        store._configured_database()
    monkeypatch.delenv("DATABASE_URL")
    secret.write_text("postgresql://owner:not-a-real-secret@database.example:not-a-port/neondb")
    with pytest.raises(store.StoreConfigurationError) as error:
        store._configured_database()
    assert "not-a-real-secret" not in str(error.value)
    secret.write_text("")
    with pytest.raises(store.StoreConfigurationError, match="empty"):
        store._configured_database()


def test_database_errors_are_explicit_but_liveness_does_not_query_database(monkeypatch):
    def failed():
        raise store.StoreUnavailable("Configured PostgreSQL is unavailable; no fallback.")
    monkeypatch.setattr(store, "check_ready", failed)
    with TestClient(api.app) as c:
        assert c.get("/api/health").status_code == 200
        res = c.get("/api/system/database")
        assert res.status_code == 503 and res.headers["retry-after"] == "10"
        assert "no fallback" in res.json()["detail"]


@pytest.mark.parametrize("mode", [None, "require", "verify-ca", "verify-full"])
def test_postgres_tls_always_authenticates_host_and_system_trust(mode, monkeypatch, tmp_path):
    from types import SimpleNamespace

    bundle = tmp_path / "system-ca.pem"
    bundle.write_text("Synthetic system trust placeholder")
    monkeypatch.setattr(store.ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=str(bundle)))
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    settings = {} if mode is None else {"sslmode": mode}
    assert store._postgres_tls(settings) == {"sslmode": "verify-full", "sslrootcert": str(bundle)}


def test_postgres_tls_preserves_custom_trust_and_rejects_unsafe_configuration(tmp_path, monkeypatch):
    root = tmp_path / "private-ca.pem"
    root.write_text("Synthetic unit-test CA placeholder")
    monkeypatch.setenv("PGSSLROOTCERT", str(root))
    assert store._postgres_tls({"sslmode": "require"})["sslrootcert"] == str(root)
    assert store._postgres_tls({"sslrootcert": "system"})["sslrootcert"] == "system"
    for mode in ("disable", "allow", "prefer", "unknown"):
        with pytest.raises(store.StoreConfigurationError, match="authenticated TLS"):
            store._postgres_tls({"sslmode": mode})
    with pytest.raises(store.StoreConfigurationError, match="trust root"):
        store._postgres_tls({"sslrootcert": str(tmp_path / "missing")})


def test_postgres_tls_missing_system_bundle_requires_explicit_trust(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.setattr(store.ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=None))
    with pytest.raises(store.StoreConfigurationError, match="No system"):
        store._postgres_tls({})
