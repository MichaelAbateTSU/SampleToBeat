"""SQLite by default, or optional Neon-compatible PostgreSQL metadata. Audio stays on disk."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .core import data_path, log

_lock = threading.RLock()
_initialized = False
_COLUMNS = "id,kind,parent,created,updated,doc"


class StoreConfigurationError(ValueError):
    pass


class StoreUnavailable(Exception):
    pass


def _configured_database() -> str:
    file = os.environ.get("CHOPLAB_DATABASE_URL_FILE")
    inline = os.environ.get("CHOPLAB_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if file and inline:
        raise StoreConfigurationError("Configure the database URL either in a private file or an environment variable, not both.")
    if file:
        try:
            inline = Path(file).read_text().strip()
        except OSError:
            raise StoreConfigurationError("Cannot read CHOPLAB_DATABASE_URL_FILE; check the private secret file.") from None
        if not inline:
            raise StoreConfigurationError("The configured database secret file is empty.")
    if inline:
        try:
            url = urlsplit(inline)
            if url.scheme not in ("postgres", "postgresql") or not url.hostname or not url.path.strip("/"):
                raise ValueError()
            _ = url.port
        except ValueError:
            raise StoreConfigurationError("The configured database must be a valid PostgreSQL URL.") from None
    return inline


_DATABASE_URL = _configured_database()


def backend_name() -> str:
    return "postgres" if _DATABASE_URL else "sqlite"


class _Postgres:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        # Only this module's fixed SQL uses the SQLite placeholder spelling.
        return self.connection.execute(query.replace("?", "%s"), params)


@contextmanager
def _postgres_conn():
    try:
        import psycopg
    except ImportError:
        raise StoreConfigurationError("PostgreSQL is configured but unavailable; install uv sync --extra postgres.") from None
    try:
        # Neon pooled URLs use PgBouncer transaction mode. Avoid session-scoped
        # prepared statements; require encrypted connections even if the URL omits TLS.
        settings = psycopg.conninfo.conninfo_to_dict(_DATABASE_URL)
        sslmode = settings.get("sslmode", "require")
        if sslmode not in ("require", "verify-ca", "verify-full"):
            raise StoreConfigurationError("PostgreSQL connections require sslmode=require, verify-ca or verify-full.")
        with psycopg.connect(_DATABASE_URL, connect_timeout=10, prepare_threshold=None, sslmode=sslmode) as con:
            yield _Postgres(con)
    except psycopg.Error as e:
        log.error("Configured PostgreSQL operation failed (%s); check private database settings.", type(e).__name__)
        raise StoreUnavailable("Configured PostgreSQL is unavailable; check its quota, network and private connection settings. No SQLite fallback was used.") from None


def _postgres_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT, "
                "created DOUBLE PRECISION, updated DOUBLE PRECISION, doc TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS docs_kind_parent ON docs(kind, parent)")
    con.execute("CREATE TABLE IF NOT EXISTS choplab_store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO choplab_store_meta(key,value) VALUES('identity',?) ON CONFLICT(key) DO NOTHING",
                (uuid.uuid4().hex,))


def _sqlite_rows() -> list[tuple]:
    path = data_path("choplab.sqlite3")
    if not path.exists():
        return []
    with closing(sqlite3.connect(path, timeout=30)) as con:
        con.execute("PRAGMA query_only=ON")
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():
            return []
        return con.execute(f"SELECT {_COLUMNS} FROM docs ORDER BY id").fetchall()


def _fingerprint(rows) -> str:
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _migration_marker(con, rows) -> dict:
    identity = con.execute("SELECT value FROM choplab_store_meta WHERE key='identity'").fetchone()[0]
    return {"postgres_identity": identity, "sqlite_fingerprint": _fingerprint(rows)}


def _write_marker(marker: dict):
    path = data_path("metadata-postgres.json")
    temp = path.with_suffix(".part")
    temp.write_text(json.dumps(marker))
    temp.replace(path)


def initialize():
    global _initialized
    if not _DATABASE_URL:
        if data_path("metadata-postgres.json").exists():
            raise StoreConfigurationError("This audio volume uses PostgreSQL metadata. Restore its private database configuration; refusing to open an older SQLite library.")
        with _conn() as con:
            con.execute("SELECT 1")
        return
    with _lock:
        if _initialized:
            return
        with _postgres_conn() as con:
            _postgres_schema(con)
            rows = _sqlite_rows()
            marker_path = data_path("metadata-postgres.json")
            expected = _migration_marker(con, rows)
            if marker_path.exists():
                try:
                    marker = json.loads(marker_path.read_text())
                except (OSError, ValueError):
                    raise StoreConfigurationError("Invalid metadata migration marker; restore the matching metadata/audio backup.") from None
                if marker != expected:
                    raise StoreConfigurationError("PostgreSQL identity or the SQLite baseline changed. Restore a matching metadata/audio backup or explicitly migrate the changed SQLite library; refusing a silent library switch.")
            elif rows:
                raise StoreConfigurationError("Existing SQLite library detected. Stop ChopLab and run python -m choplab.metadata migrate-sqlite before switching to PostgreSQL. No automatic migration was performed.")
        if not marker_path.exists():
            _write_marker(expected)
        _initialized = True


def check_ready():
    initialize()
    with _conn() as con:
        con.execute("SELECT 1").fetchone()
    return {"ok": True, "metadata_store": backend_name()}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return round(time.time(), 3)


@contextmanager
def _conn():
    if _DATABASE_URL:
        initialize()
        with _postgres_conn() as con:
            yield con
        return
    if data_path("metadata-postgres.json").exists():
        raise StoreConfigurationError("This volume uses PostgreSQL; refusing a silent SQLite fallback.")
    con = sqlite3.connect(data_path("choplab.sqlite3"), timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT, "
            "created REAL, updated REAL, doc TEXT NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS docs_kind_parent ON docs(kind, parent)")
        yield con
        con.commit()
    finally:
        con.close()


def put(kind: str, doc: dict, parent: str | None = None) -> dict:
    parent = parent if parent is not None else doc.get("_parent")
    doc["_parent"] = parent
    doc.setdefault("created_at", now())
    doc["updated_at"] = now()
    with _lock, _conn() as con:
        raw = json.dumps(doc, default=_plain)
        con.execute(
            "INSERT INTO docs(id,kind,parent,created,updated,doc) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, updated=excluded.updated, parent=excluded.parent",
            (doc["id"], kind, parent, doc["created_at"], doc["updated_at"], raw))
    return json.loads(raw)  # what was stored, as plain JSON types


def _plain(o):
    if hasattr(o, "tolist"):  # numpy scalars and arrays from the DSP code
        return o.tolist()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def get(kind: str, id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT doc FROM docs WHERE id=? AND kind=?", (id, kind)).fetchone()
    return json.loads(row[0]) if row else None


def list_(kind: str, parent: str | None = None) -> list[dict]:
    with _conn() as con:
        if parent is None:
            rows = con.execute("SELECT doc FROM docs WHERE kind=? ORDER BY updated DESC", (kind,)).fetchall()
        else:
            rows = con.execute("SELECT doc FROM docs WHERE kind=? AND parent=? ORDER BY created", (kind, parent)).fetchall()
    return [json.loads(r[0]) for r in rows]


def update(kind: str, id: str, **fields) -> dict:
    with _lock:  # read-modify-write under one lock: no lost updates between job threads
        doc = get(kind, id)
        if doc is None:
            raise KeyError(id)
        doc.update(fields)
        return put(kind, doc, doc.get("_parent"))


def delete(kind: str, id: str) -> None:
    with _lock, _conn() as con:
        con.execute("DELETE FROM docs WHERE id=? AND kind=?", (id, kind))


def export_snapshot() -> dict:
    with _lock, _conn() as con:
        rows = con.execute(f"SELECT {_COLUMNS} FROM docs ORDER BY id").fetchall()
    return {"format": "choplab-metadata-1", "metadata_store": backend_name(), "rows": rows}


def _validate_snapshot(snapshot: dict) -> list:
    if not isinstance(snapshot, dict) or snapshot.get("format") != "choplab-metadata-1":
        raise StoreConfigurationError("Not a supported ChopLab metadata snapshot.")
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise StoreConfigurationError("Metadata snapshot is missing rows.")
    ids = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise StoreConfigurationError("Invalid metadata snapshot row.")
        id, kind, parent, created, updated, raw = row
        if (not isinstance(id, str) or not isinstance(kind, str) or id in ids
                or not (parent is None or isinstance(parent, str))
                or not isinstance(created, (int, float)) or not math.isfinite(created)
                or not isinstance(updated, (int, float)) or not math.isfinite(updated)
                or not isinstance(raw, str)):
            raise StoreConfigurationError("Invalid metadata snapshot fields.")
        try:
            doc = json.loads(raw)
        except ValueError:
            raise StoreConfigurationError("Invalid metadata JSON in snapshot.") from None
        if not isinstance(doc, dict) or doc.get("id") != id:
            raise StoreConfigurationError("Metadata snapshot document ID does not match its row.")
        ids.add(id)
    return rows


def restore_snapshot(snapshot: dict, *, migrating: bool = False):
    """Explicit offline import into an empty store. Existing records are never overwritten."""
    global _initialized
    rows = _validate_snapshot(snapshot)
    if migrating and not _DATABASE_URL:
        raise StoreConfigurationError("Configure a private PostgreSQL URL before migrating SQLite.")
    with _lock:
        if _DATABASE_URL:
            with _postgres_conn() as con:
                _postgres_schema(con)
                existing = con.execute(f"SELECT {_COLUMNS} FROM docs ORDER BY id").fetchall()
                if existing and existing != [tuple(r) for r in sorted(rows, key=lambda r: r[0])]:
                    raise StoreConfigurationError("Destination PostgreSQL store is not empty; refusing to overwrite it.")
                if not existing:
                    for row in rows:
                        con.execute(f"INSERT INTO docs({_COLUMNS}) VALUES(?,?,?,?,?,?)", row)
                marker = _migration_marker(con, _sqlite_rows())
            _write_marker(marker)
            _initialized = False
            initialize()
        else:
            with _conn() as con:
                if con.execute("SELECT 1 FROM docs LIMIT 1").fetchone():
                    raise StoreConfigurationError("Destination SQLite store is not empty; refusing to overwrite it.")
                for row in rows:
                    con.execute(f"INSERT INTO docs({_COLUMNS}) VALUES(?,?,?,?,?,?)", row)


def migrate_sqlite():
    rows = _sqlite_rows()
    if not rows:
        raise StoreConfigurationError("No existing SQLite records to migrate.")
    restore_snapshot({"format": "choplab-metadata-1", "rows": rows}, migrating=True)
