"""Private durable assets and database-fenced runtime ownership.

An asset publication gets a never-reused namespace before any S3 write. Retirement
is permanent, so delayed deletes cannot affect a successor's identical bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from . import core
from .object_storage import ObjectRef, S3ByteStorage, S3Settings, StorageError


class OwnershipLost(StorageError):
    pass


_settings: S3Settings | None = None
_owner: str | None = None
_fence: int | None = None
_ready = False
_runtime_error: str | None = None
_stop = threading.Event()
_cache_lock = threading.RLock()
_transfer_lock = threading.RLock()
LEASE_SECONDS = 60
_PATH_FIELDS = {"original_file_path", "internal_audio_path", "preview_path", "sample_path",
                "audio_path", "path", "folder", "features_path"}


def enabled() -> bool:
    return _settings is not None


def require_config(doc):
    if doc.get("_assets") and not enabled():
        raise StorageError("This library uses durable audio. Restore its private CHOPLAB_STORAGE_CONFIG_FILE; no local audio fallback was used.")


def configure():
    global _settings
    secret = os.environ.get("CHOPLAB_STORAGE_CONFIG_FILE")
    if not secret:
        _settings = None
        return
    try:
        settings = json.loads(Path(secret).read_text())
        _settings = S3Settings(**settings)
    except (OSError, ValueError, TypeError):
        raise StorageError("Invalid private CHOPLAB_STORAGE_CONFIG_FILE; configure explicit HTTPS storage credentials and limits.") from None
    from . import store
    if store.backend_name() != "postgres":
        raise StorageError("Durable object storage requires PostgreSQL metadata; no SQLite fallback is allowed.")
    # Validate namespace length before accepting writes with no cleanup path.
    replace(_settings, prefix=f"{_settings.prefix}/publications/9999999999999999999/{uuid.uuid4().hex}")


def _schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS choplab_runtime (id INTEGER PRIMARY KEY, "
                "owner TEXT NOT NULL, fence BIGINT NOT NULL, expires DOUBLE PRECISION NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS choplab_assets (id TEXT PRIMARY KEY, relative TEXT NOT NULL, "
                "namespace TEXT NOT NULL, state TEXT NOT NULL, ref TEXT, owner_fence BIGINT NOT NULL, "
                "upload_id TEXT, created DOUBLE PRECISION NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS choplab_asset_links (doc_id TEXT NOT NULL, asset_id TEXT NOT NULL, "
                "PRIMARY KEY(doc_id,asset_id))")


def guard(con):
    if not enabled():
        return
    row = con.execute("SELECT owner,fence,expires,EXTRACT(EPOCH FROM clock_timestamp()) "
                      "FROM choplab_runtime WHERE id=1 FOR UPDATE").fetchone()
    if not row or row[0] != _owner or row[1] != _fence or row[2] <= row[3]:
        raise OwnershipLost("This instance no longer owns processing. Reload after the replacement instance starts; no stale result was committed.")


def readiness_error():
    if not enabled() or _ready:
        return None
    return _runtime_error or "Processing is waiting for exclusive storage ownership/reconciliation. Health remains available; reload the studio after the old instance releases its lease."


def check():
    error = readiness_error()
    if error:
        raise OwnershipLost(error)
    _check_ownership()


def _check_ownership():
    from . import store
    with store._conn() as con:
        guard(con)


def restore_kits():
    from . import drums, store
    docs = store.list_("kit")
    for kit in drums.KITS:
        for inst in drums.INSTRUMENTS:
            if not any(doc["id"] == f"kit-{kit}-{inst}" for doc in docs):
                core.data_path("kits", kit, "custom", f"{inst}.wav").unlink(missing_ok=True)
    drums._sound.cache_clear()


def migrate_local():
    """Explicitly enabling storage publishes the existing volume before replacement."""
    from . import store
    with store._conn() as con:
        pending = con.execute("SELECT doc FROM docs WHERE kind='job'").fetchall()
    if any((job := json.loads(row[0])).get("status") in ("queued", "running")
           and job.get("owner_volume") != "durable" for row in pending):
        from .jobs import RECOVERY_COMMAND
        raise StorageError(
            "Legacy or unowned PostgreSQL jobs cannot be fenced by this storage runtime and were left untouched. "
            f"Stop ALL legacy runtimes, back up metadata/audio, and run {RECOVERY_COMMAND} "
            "without CHOPLAB_STORAGE_CONFIG_FILE on the matching original volume before enabling durable storage.")
    for kind in ("source", "project", "variation", "export", "preview", "sound", "kit", "job"):
        with store._conn() as con:
            rows = con.execute("SELECT doc FROM docs WHERE kind=?", (kind,)).fetchall()
        for row in rows:
            doc = json.loads(row[0])
            if doc.get("_assets") is not None:
                continue
            if kind == "source" and doc.get("analysis"):
                features = core.DATA_DIR / "projects" / doc["project_id"] / "sources" / doc["id"] / "features.npz"
                if features.exists():
                    doc["features_path"] = str(features)
                else:
                    raise StorageError("Existing source features are missing. Restore the original volume before enabling durable storage.")
            store.put(kind, doc)
    # Existing custom kits were historically filesystem-only.
    from . import drums
    for kit in drums.KITS:
        for inst in drums.INSTRUMENTS:
            path = core.data_path("kits", kit, "custom", f"{inst}.wav")
            if path.is_file() and not store.get("kit", f"kit-{kit}-{inst}"):
                store.put("kit", {"id": f"kit-{kit}-{inst}", "audio_path": str(path)})


def portable(doc, field=""):
    """Serialize exported project metadata without a host-specific cache root."""
    if isinstance(doc, dict):
        return {k: {name: portable(path, "stems-value") for name, path in v.items()}
                if k == "stems" and isinstance(v, dict) else portable(v, k)
                for k, v in doc.items()}
    if isinstance(doc, list):
        return [portable(v, field) for v in doc]
    if isinstance(doc, str) and (field in _PATH_FIELDS or field == "stems-value") and Path(doc).is_absolute():
        return "asset-path:" + _relative(doc)
    return doc


def portable_project(doc):
    from . import store
    catalog = {}
    def collect(value):
        if isinstance(value, dict):
            for relative, id in value.get("_assets", {}).items():
                with store._conn() as con:
                    row = _asset(con, id)
                catalog[id] = {"relative": relative, "object": json.loads(row[3])}
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(doc)
    result = portable(doc)
    result["durable_assets"] = catalog
    return result


@contextmanager
def runtime(*, on_acquired=None):
    # Cache files are mutable DSP working files, private to one process even
    # though authoritative metadata and immutable objects are shared.
    with open(core.data_path("durable-cache.lock"), "a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise OwnershipLost("Another process owns this local audio cache. Use a private cache directory per instance.") from None
        try:
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
                lock.flush()
            with _runtime(on_acquired=on_acquired):
                yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _runtime(*, on_acquired=None):
    global _owner, _fence, _ready, _runtime_error
    from . import store
    owner = uuid.uuid4().hex
    def acquire():
        global _owner, _fence
        with store._conn() as con:
            _schema(con)
            con.execute("INSERT INTO choplab_runtime(id,owner,fence,expires) VALUES(1,'',0,0) ON CONFLICT(id) DO NOTHING")
            row = con.execute("SELECT fence,expires,EXTRACT(EPOCH FROM clock_timestamp()) "
                              "FROM choplab_runtime WHERE id=1 FOR UPDATE").fetchone()
            if row[1] > row[2]:
                return False
            highest = con.execute("SELECT COALESCE(MAX(owner_fence),0) FROM choplab_assets").fetchone()[0]
            _owner, _fence = owner, max(row[0], highest) + 1
            con.execute("UPDATE choplab_runtime SET owner=?,fence=?,expires=EXTRACT(EPOCH FROM clock_timestamp())+? WHERE id=1",
                        (owner, _fence, LEASE_SECONDS))
            return True
    _ready = False
    _runtime_error = None
    _stop.clear()

    def heartbeat():
        global _ready, _runtime_error
        while not _stop.wait(10):
            try:
                with store._conn() as con:
                    guard(con)
                    con.execute("UPDATE choplab_runtime SET expires=EXTRACT(EPOCH FROM clock_timestamp())+? WHERE id=1",
                                (LEASE_SECONDS,))
            except (StorageError, store.StoreUnavailable) as error:
                core.log.error("Runtime ownership renewal failed: %s", error)
                _ready = False
                _runtime_error = f"Runtime ownership renewal failed: {error} Processing is read-only; resolve the database/storage error and restart this instance."
                return

    renewal = None
    def activate():
        nonlocal renewal
        global _ready
        renewal = threading.Thread(target=heartbeat, name="storage-lease", daemon=True)
        renewal.start()
        migrate_local()
        reconcile()
        restore_kits()
        if on_acquired:
            on_acquired()
        if _runtime_error:
            raise OwnershipLost(_runtime_error)
        with store._conn() as con:
            guard(con)
        _ready = True
    def supervise():
        global _runtime_error
        while not _stop.wait(5):
            try:
                if acquire():
                    activate()
                    return
            except (StorageError, store.StoreUnavailable) as error:
                core.log.error("Replacement ownership/reconciliation failed: %s", error)
                _runtime_error = f"Replacement ownership/reconciliation failed: {error} Processing is read-only; resolve the database/storage error and restart this instance."
                # A partially reconciled owner must not admit API mutations.
                return
    supervisor = None
    try:
        if acquire():
            try:
                activate()
            except (StorageError, store.StoreUnavailable) as error:
                _runtime_error = f"Storage initialization/reconciliation failed: {error} Processing is read-only; resolve the error and restart this instance."
                core.log.error("%s", _runtime_error)
        else:
            core.log.warning("Waiting as health-ready/read-only replacement; current owner was not interrupted.")
            supervisor = threading.Thread(target=supervise, name="storage-takeover", daemon=True)
            supervisor.start()
        yield
    finally:
        _stop.set()
        if supervisor:
            supervisor.join()
        if renewal:
            renewal.join()
        if _owner == owner:
            with store._conn() as con:
                con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1 AND owner=? AND fence=?",
                            (owner, _fence))
        _ready = False
        _owner, _fence = None, None


def _adapter(namespace: str, *, callback=None):
    if not _settings or not namespace.startswith(_settings.prefix + "/publications/"):
        raise StorageError("Asset namespace does not belong to the configured private storage.")
    return S3ByteStorage(replace(_settings, prefix=namespace), core.DATA_DIR, on_multipart_started=callback)


def _relative(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise StorageError("Audio paths must be absolute server-generated paths before publication.")
    try:
        return path.resolve().relative_to(core.DATA_DIR.resolve()).as_posix()
    except ValueError:
        raise StorageError("Referenced audio is outside the private audio cache; reimport it before saving.") from None


def _publish(relative: str) -> str:
    from . import store
    id = uuid.uuid4().hex
    namespace = f"{_settings.prefix}/publications/{_fence}/{id}"
    with store._conn() as con:
        guard(con)
        con.execute("INSERT INTO choplab_assets(id,relative,namespace,state,owner_fence,created) "
                    "VALUES(?,?,?,'uploading',?,EXTRACT(EPOCH FROM clock_timestamp()))",
                    (id, relative, namespace, _fence))

    def record(key, upload_id):
        with store._conn() as con:
            guard(con)
            con.execute("UPDATE choplab_assets SET upload_id=? WHERE id=? AND state='uploading'", (upload_id, id))

    try:
        with _transfer_lock:
            ref = _adapter(namespace, callback=record).publish(relative)
    except StorageError as error:
        raise StorageError(f"{error}. Check storage quota, branch credentials and throttling (503 SlowDown); retry after the provider recovers. Deletion may not immediately reclaim quota.") from None
    with store._conn() as con:
        guard(con)
        con.execute("UPDATE choplab_assets SET state='published',ref=?,upload_id=NULL WHERE id=? AND state='uploading'",
                    (json.dumps(asdict(ref)), id))
    return id


def _asset(con, id):
    row = con.execute("SELECT relative,namespace,state,ref FROM choplab_assets WHERE id=?", (id,)).fetchone()
    if not row:
        raise StorageError("Durable audio reference is missing from PostgreSQL; restore matching metadata and storage.")
    return row


def prepare(doc: dict) -> dict:
    """Publish stable files before a fenced metadata commit; never store cache roots."""
    from . import store
    prior = doc.get("_assets", {})
    assets: dict[str, str] = {}
    result = json.loads(json.dumps(doc, default=store._plain))

    def encode(value, field=""):
        if isinstance(value, dict):
            return {key: {name: encode(path, "stems-value") for name, path in item.items()}
                    if key == "stems" and isinstance(item, dict) else encode(item, key)
                    for key, item in value.items() if key != "_assets"}
        if isinstance(value, list):
            return [encode(item, field) for item in value]
        if not isinstance(value, str) or not value:
            return value
        if value.startswith(("asset-path:", "asset-folder:")):
            relative = value.split(":", 1)[1]
        elif field in _PATH_FIELDS or field == "stems-value":
            relative = _relative(value)
        else:
            return value
        path = core.DATA_DIR.joinpath(*relative.split("/"))
        if field == "folder":
            return "asset-folder:" + relative
        if relative in assets:
            return "asset-path:" + relative
        old = prior.get(relative)
        if old:
            with store._conn() as con:
                row = _asset(con, old)
            if row[2] != "live":
                raise StorageError("Retired audio cannot gain new references; republish the asset with a new key.")
            ref = ObjectRef(**json.loads(row[3]))
            if not path.exists() or _hash(path) == ref.sha256:
                assets[relative] = old
                return "asset-path:" + relative
        if not path.is_file():
            raise StorageError("Referenced audio file is missing; restore it or import it again. No metadata was committed.")
        assets[relative] = _publish(relative)
        return "asset-path:" + relative

    result = encode(result)
    result["_assets"] = assets
    return result


def _hash(path):
    from .object_storage import _release_file_cache
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            _release_file_cache(stream)
        return digest.hexdigest()


def materialize(id: str) -> Path:
    from . import store
    with _cache_lock:
        with store._conn() as con:
            relative, namespace, state, raw = _asset(con, id)
        if state != "live":
            raise StorageError("Audio is retired or not yet published; no partial file was served.")
        ref = ObjectRef(**json.loads(raw))
        adapter = _adapter(namespace)
        path = adapter._path(relative)
        if path.is_file() and path.stat().st_size == ref.size and _hash(path) == ref.sha256:
            return path
        try:
            with _transfer_lock:
                return adapter.materialize(ref, relative)
        except StorageError as error:
            raise StorageError(f"{error}. Check quota, private branch credentials, 503 SlowDown and local disk reserve; no incomplete audio was served.") from None


def ensure_path(value: str | Path, doc: dict | None = None) -> Path:
    """Restore only the authoritative asset a consumer is about to read."""
    path = Path(value)
    if not enabled():
        return path
    from . import store
    relative = _relative(str(path))
    if doc is not None:
        id = doc.get("_assets", {}).get(relative)
        if not id:
            raise StorageError("Required audio has no durable reference; no local fallback was used.")
        with store._conn() as con:
            if _asset(con, id)[0] != relative:
                raise StorageError("Required audio path disagrees with its durable reference.")
    else:
        with store._conn() as con:
            rows = con.execute("SELECT DISTINCT a.id,a.ref FROM choplab_assets a "
                               "JOIN choplab_asset_links l ON l.asset_id=a.id "
                               "WHERE a.relative=? AND a.state='live'", (relative,)).fetchall()
        # Arrangements and custom sounds can publish the same path independently.
        # Only byte-identical live publications are interchangeable, not newer
        # content, ETags or retired keys.
        references = [ObjectRef(**json.loads(row[1])) for row in rows]
        if not rows or len({(ref.size, ref.sha256) for ref in references}) != 1:
            raise StorageError("Required audio reference is missing or ambiguous; no local fallback was used.")
        id = rows[0][0]
    return materialize(id)


def restore(doc: dict, *, hydrate: bool = False) -> dict:
    assets = doc.get("_assets", {})
    if hydrate:
        for id in set(assets.values()):
            materialize(id)

    def decode(value):
        if isinstance(value, dict):
            return {k: decode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [decode(v) for v in value]
        if isinstance(value, str) and value.startswith(("asset-path:", "asset-folder:")):
            relative = value.split(":", 1)[1]
            path = core.DATA_DIR.joinpath(*relative.split("/"))
            if not path.resolve().is_relative_to(core.DATA_DIR.resolve()):
                raise StorageError("Portable audio path escapes the private cache.")
            return str(path)
        return value
    return decode(doc)


def link(con, doc: dict):
    """Called in the same guarded DB transaction as the authoritative doc write."""
    new = set(doc.get("_assets", {}).values())
    for id in new:
        row = _asset(con, id)
        if row[2] not in ("published", "live"):
            raise StorageError("Retired or incomplete audio cannot be referenced.")
        con.execute("UPDATE choplab_assets SET state='live' WHERE id=?", (id,))
    con.execute("DELETE FROM choplab_asset_links WHERE doc_id=?", (doc["id"],))
    for id in new:
        con.execute("INSERT INTO choplab_asset_links(doc_id,asset_id) VALUES(?,?)", (doc["id"], id))
    retire_unlinked(con)


def retire_unlinked(con):
    con.execute("UPDATE choplab_assets SET state='retired' WHERE state='live' "
                "AND NOT EXISTS(SELECT 1 FROM choplab_asset_links WHERE asset_id=choplab_assets.id)")


def reconcile():
    """Permanently retire old orphan publications, then retry exact-key cleanup."""
    from . import store
    with store._conn() as con:
        guard(con)
        con.execute("UPDATE choplab_assets SET state='retired' WHERE state IN ('uploading','published') AND owner_fence<?", (_fence,))
        retire_unlinked(con)
        rows = con.execute("SELECT id,namespace,ref,upload_id FROM choplab_assets WHERE state IN ('retired','deleted')").fetchall()
    for id, namespace, raw, upload in rows:
        _check_ownership()
        adapter = _adapter(namespace)
        if raw:
            adapter.delete(ObjectRef(**json.loads(raw)))
        else:
            # A crash after PUT/complete but before recording the ref can leave an
            # orphan. List only this retired, never-reused publication namespace.
            with adapter._operation("orphan cleanup"):
                pages = adapter.client.get_paginator("list_objects_v2").paginate(
                    Bucket=adapter.settings.bucket, Prefix=namespace + "/objects/")
                for page in pages:
                    for item in page.get("Contents", []):
                        _check_ownership()
                        adapter.client.delete_object(Bucket=adapter.settings.bucket, Key=item["Key"])
            for item in adapter.incomplete_uploads():
                _check_ownership()
                adapter.abort_upload(item["Key"], item["UploadId"])
        with store._conn() as con:
            guard(con)
            con.execute("UPDATE choplab_assets SET state='deleted' WHERE id=? AND state='retired'", (id,))


def snapshot(con):
    if not con.execute("SELECT to_regclass('choplab_assets')").fetchone()[0]:
        return None
    return {"assets": con.execute("SELECT id,relative,namespace,state,ref,owner_fence,upload_id,created "
                                  "FROM choplab_assets ORDER BY id").fetchall(),
            "links": con.execute("SELECT doc_id,asset_id FROM choplab_asset_links ORDER BY doc_id,asset_id").fetchall()}


def validate_snapshot(data, docs):
    from .object_storage import _safe_name
    wanted = {(doc["id"], id) for doc in docs for id in doc.get("_assets", {}).values()}
    if not data:
        if wanted:
            raise StorageError("Snapshot has audio references but no asset catalog; refusing an incomplete restore.")
        return
    if not isinstance(data, dict) or set(data) != {"assets", "links"}:
        raise StorageError("Invalid durable audio snapshot.")
    ids = set()
    live = set()
    for row in data["assets"]:
        if not isinstance(row, (list, tuple)) or len(row) != 8:
            raise StorageError("Invalid asset catalog row.")
        id, relative, namespace, state, raw, fence, upload, created = row
        if not isinstance(id, str) or id in ids or state not in ("uploading", "published", "live", "retired", "deleted"):
            raise StorageError("Invalid asset catalog identity or state.")
        _safe_name(relative, local=True)
        _safe_name(namespace)
        if not isinstance(fence, int) or not isinstance(created, (int, float)) or (upload is not None and not isinstance(upload, str)):
            raise StorageError("Invalid asset catalog fields.")
        if raw:
            ref = ObjectRef(**json.loads(raw))
            if not ref.key.startswith(namespace + "/objects/"):
                raise StorageError("Asset namespace and object reference disagree.")
        elif state in ("live", "published"):
            raise StorageError("Live asset has no verified reference.")
        ids.add(id)
        if state == "live":
            live.add(id)
    links = {tuple(row) for row in data["links"]}
    if links != wanted or any(id not in live for _, id in links):
        raise StorageError("Durable snapshot references do not match live assets.")


def restore_snapshot(con, data):
    _schema(con)
    row = con.execute("SELECT expires,EXTRACT(EPOCH FROM clock_timestamp()) FROM choplab_runtime WHERE id=1 FOR UPDATE").fetchone()
    if row and row[0] > row[1]:
        raise OwnershipLost("Stop all runtimes before restoring metadata/audio.")
    if con.execute("SELECT 1 FROM choplab_assets LIMIT 1").fetchone():
        raise StorageError("Destination asset catalog is not empty; refusing to overwrite tombstones or live objects.")
    for row in data["assets"]:
        con.execute("INSERT INTO choplab_assets(id,relative,namespace,state,ref,owner_fence,upload_id,created) VALUES(?,?,?,?,?,?,?,?)", row)
    for row in data["links"]:
        con.execute("INSERT INTO choplab_asset_links(doc_id,asset_id) VALUES(?,?)", row)
