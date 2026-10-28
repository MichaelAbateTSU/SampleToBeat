"""Project management service: SQLite metadata store (documents as JSON, audio stays on disk)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .core import data_path

_lock = threading.RLock()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return round(time.time(), 3)


@contextmanager
def _conn():
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
