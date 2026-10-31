"""Synthetic metadata checks for a disposable PostgreSQL database, executed inside the app container."""
import argparse
import json
import sqlite3
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from choplab import jobs, store
from choplab.core import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("phase", choices=["tls", "crud", "restart", "migration", "migration-restart", "restore"])
    args = parser.parse_args()
    assert store.backend_name() == "postgres", "These tests require explicitly configured PostgreSQL."

    if args.phase == "tls":
        import psycopg

        original = store._DATABASE_URL
        settings = psycopg.conninfo.conninfo_to_dict(original)
        verified = store._postgres_tls(settings)
        assert verified["sslmode"] == "verify-full"
        with store._postgres_conn() as con:
            assert con.connection.pgconn.ssl_in_use
            assert con.connection.info.get_parameters()["sslmode"] == "verify-full"
        hostaddr = socket.gethostbyname(settings["host"])
        cases = [
            ({"sslrootcert": "/run/secrets/postgres_untrusted_ca"}, "certificate verify failed"),
            ({"host": "hostname-mismatch.invalid", "hostaddr": hostaddr}, "does not match host name"),
        ]
        for overrides, reason in cases:
            store._DATABASE_URL = psycopg.conninfo.make_conninfo(original, **overrides)
            bad_settings = psycopg.conninfo.conninfo_to_dict(store._DATABASE_URL)
            try:
                with psycopg.connect(store._DATABASE_URL, connect_timeout=10,
                                     **store._postgres_tls(bad_settings)):
                    raise AssertionError("Unsafe PostgreSQL TLS peer was accepted.")
            except psycopg.OperationalError as error:
                assert reason in str(error), "Failure was not the expected certificate verification rejection."
            try:
                with store._postgres_conn():
                    raise AssertionError("Store accepted unsafe PostgreSQL TLS peer.")
            except store.StoreUnavailable as error:
                assert "hostname-mismatch" not in str(error), "Private connection details leaked."
        store._DATABASE_URL = original
        print("Verified TLS accepted trusted/matched PostgreSQL and rejected untrusted and hostname-mismatched peers.")
        return

    if args.phase == "crud":
        assert store.check_ready() == {"ok": True, "metadata_store": "postgres"}
        with store._postgres_conn() as con:
            assert con.connection.prepare_threshold is None
            assert con.connection.pgconn.ssl_in_use
        assert store.list_("project") == [], "Use an empty, disposable test database."
        parent = store.new_id("pg_test")
        doc = store.put("project", {"id": parent, "name": "Quotes ' ; DROP TABLE docs; --", "count": 0})
        assert store.get("project", parent) == doc
        assert abs(doc["created_at"] - store.list_("project")[0]["created_at"]) < 0.001
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: store.update("project", parent, **{f"field_{n}": n}), range(8)))
        assert all(store.get("project", parent)[f"field_{n}"] == n for n in range(8))
        updated = store.update("project", parent, name="PostgreSQL persisted", count=8)
        assert store.get("project", parent) == updated
        child = store.put("sample", {"id": store.new_id("pg_child"), "notes": [1, 2, 3]}, parent=parent)
        assert store.list_("sample", parent) == [child]
        store.delete("sample", child["id"])
        assert store.get("sample", child["id"]) is None

        @jobs.handler("postgres-test")
        def handler(ctx):
            ctx.progress("database progress", 0.5)
            return {"verified": True}

        job = jobs.submit("postgres-test", parent, {})
        result = jobs.wait(job["id"], timeout=30)
        assert result["status"] == "completed" and result["result"] == {"verified": True}
        interrupted = store.put("job", {"id": store.new_id("pg_interrupted"), "status": "running"}, parent=parent)
        snapshot = store.export_snapshot()
        assert snapshot["metadata_store"] == "postgres"
        bad = {**snapshot, "rows": snapshot["rows"] + [["invalid", "project", None, 1, 2, '{"id":"wrong"}']]}
        try:
            store.restore_snapshot(bad)
        except store.StoreConfigurationError:
            pass
        else:
            raise AssertionError("Invalid restore was accepted")
        assert store.get("project", parent)["name"] == "PostgreSQL persisted"
        args.state.write_text(json.dumps({"parent": parent, "job": job["id"], "interrupted": interrupted["id"],
                                         "snapshot": snapshot}))
        print("Real PostgreSQL CRUD, precision, parameterization, threaded updates, job progress and snapshot checks passed.")
        return

    state = json.loads(args.state.read_text())
    if args.phase == "restart":
        store.initialize()
        jobs.recover_interrupted()
        assert store.get("project", state["parent"])["name"] == "PostgreSQL persisted"
        assert store.get("job", state["job"])["status"] == "completed"
        assert store.get("job", state["interrupted"])["status"] == "interrupted"
        for kind, id in [("job", state["job"]), ("job", state["interrupted"]), ("project", state["parent"])]:
            store.delete(kind, id)
        print("A fresh container process retained PostgreSQL metadata and recovered interrupted jobs.")
    elif args.phase == "migration":
        assert store.list_("project") == []
        legacy = {"id": store.new_id("legacy"), "name": "Explicit SQLite migration", "_parent": None,
                  "created_at": 1761940000.123, "updated_at": 1761940001.456}
        with closing(sqlite3.connect(DATA_DIR / "choplab.sqlite3")) as con:
            con.execute("CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT, "
                        "created REAL, updated REAL, doc TEXT NOT NULL)")
            assert con.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0
            con.execute("INSERT INTO docs VALUES(?,?,?,?,?,?)",
                        (legacy["id"], "project", None, legacy["created_at"], legacy["updated_at"], json.dumps(legacy)))
            con.commit()
        store._initialized = False
        try:
            store.initialize()
        except store.StoreConfigurationError:
            pass
        else:
            raise AssertionError("Existing SQLite library was silently hidden")
        store.migrate_sqlite()
        assert store.get("project", legacy["id"]) == legacy
        store.update("project", legacy["id"], name="PostgreSQL changed after migration")
        state["legacy"] = legacy["id"]
        args.state.write_text(json.dumps(state))
        print("Explicit atomic SQLite migration retained IDs/timestamps/audio paths and blocked silent switching.")
    elif args.phase == "migration-restart":
        store.initialize()
        assert store.get("project", state["legacy"])["name"] == "PostgreSQL changed after migration"
        store.delete("project", state["legacy"])
        store._initialized = False
        store.initialize()
        assert store.get("project", state["legacy"]) is None
        print("Migrated metadata changes and deletion survived restart without reopening legacy SQLite.")
    else:
        store.restore_snapshot(state["snapshot"])
        assert store.get("project", state["parent"])["name"] == "PostgreSQL persisted"
        for row in state["snapshot"]["rows"]:
            store.delete(row[1], row[0])
        print("PostgreSQL application-metadata snapshot restored into an empty destination.")


if __name__ == "__main__":
    main()
