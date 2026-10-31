"""Disposable real multipart/save preflight. No product routes or database startup."""
from __future__ import annotations

import asyncio
import errno
import hashlib
import hmac
import importlib.metadata
import inspect
import json
import os
import resource
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

TOKEN = os.environ.get("PROBE_TOKEN", "")
if len(TOKEN) < 32:
    raise RuntimeError("PROBE_TOKEN needs at least 32 characters")
MODE = os.environ.get("PROBE_DURABLE_BRANCH", "true")
if MODE not in ("true", "false"):
    raise RuntimeError("PROBE_DURABLE_BRANCH must be true or false")

# Hosted config is synthetic and local. OwnerAccess/product routes are not served.
salt = secrets.token_bytes(16)
password_file = Path("/tmp/probe-owner-hash")
password_file.write_text(
    "pbkdf2_sha256$600000$" + salt.hex() + "$"
    + hashlib.pbkdf2_hmac("sha256", TOKEN.encode(), salt, 600000).hex()
)
password_file.chmod(0o600)
os.environ["CHOPLAB_PASSWORD_HASH_FILE"] = str(password_file)

from choplab import api, durable, object_storage  # noqa: E402
from fastapi import FastAPI, File, UploadFile  # noqa: E402
from starlette import formparsers  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

if api.MAX_UPLOAD != 1024 * (1 << 20) or api.MIN_FREE != 2048 * (1 << 20):
    raise RuntimeError("Production upload cap/reserve must be 1024/2048 MiB")
if tempfile.gettempdir() != "/tmp" or api.DATA_DIR != Path("/data"):
    raise RuntimeError("Probe requires production /tmp and /data paths")
api.DATA_DIR.mkdir(exist_ok=True)
# No configure(), DB, SDK client or remote resource. Only enabled()'s predicate
# is forced to exercise the unchanged function's production cache-release calls.
durable._settings = object() if MODE == "true" else None

SOURCE = Path("/source-sha").read_text().strip()
SOURCE_HASHES = {
    name: hashlib.sha256(inspect.getsource(obj).encode()).hexdigest()
    for name, obj in (
        ("save_upload", api._save_upload),
        ("release_file_cache", object_storage._release_file_cache),
        ("multipart_parser", formparsers.MultiPartParser),
    )
}


def filesystem(path):
    st, fs = os.stat(path), os.statvfs(path)
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        head, tail = line.split(" - ", 1)
        fields, info = head.split(), tail.split()
        mount = fields[4]
        if path == mount or path.startswith(mount.rstrip("/") + "/"):
            mounts.append((len(mount), {"mountpoint": mount, "device": fields[2],
                                       "filesystem_type": info[0]}))
    identity = max(mounts, key=lambda item: item[0])[1] if mounts else {}
    return {"path": path, "st_dev": st.st_dev, "free_bytes": fs.f_bavail * fs.f_frsize,
            "total_bytes": fs.f_blocks * fs.f_frsize,
            "free_inodes": fs.f_favail, **identity}


def snapshot():
    cg = {}
    for name in ("memory.current", "memory.peak", "memory.max", "memory.events"):
        p = Path("/sys/fs/cgroup") / name
        if p.exists():
            cg[name] = p.read_text().strip()
    return {"tmp": filesystem("/tmp"), "data": filesystem("/data"),
            "rss_max_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "cgroup": cg}


ACTIVE = None
GATE = threading.Lock()
ORIGINAL_SPOOL = formparsers.SpooledTemporaryFile
ORIGINAL_RELEASE = object_storage._release_file_cache


def tracked_spool(*args, **kwargs):
    spool = ORIGINAL_SPOOL(*args, **kwargs)
    if ACTIVE is not None:
        ACTIVE["spools"].append(spool)
    return spool


def tracked_release(stream, *, written=False):
    if ACTIVE is not None:
        ACTIVE["release_calls"] += 1
    return ORIGINAL_RELEASE(stream, written=written)


formparsers.SpooledTemporaryFile = tracked_spool
object_storage._release_file_cache = tracked_release


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            h.update(chunk)
            # Avoid accumulating extra dirty/read page cache from test hashing.
            ORIGINAL_RELEASE(stream)
    return h.hexdigest()


app = FastAPI()


@app.get("/healthz")
async def health():
    return {"ok": True, "source_sha": SOURCE}


@app.post("/probe")
async def probe(file: UploadFile = File(...)):
    state = ACTIVE
    state["phase"] = "save"
    state["after_parse"] = snapshot()
    state["file_size_parser"] = file.size
    state["spool_rolled"] = bool(getattr(file.file, "_rolled", False))
    state["spool_device"] = os.fstat(file.file.fileno()).st_dev
    dest = api.DATA_DIR / "probe-upload.bin"
    state["destination"] = str(dest)
    try:
        n = await api._save_upload(file, dest)
        state["phase"] = "digest"
        state["after_save"] = snapshot()
        state["saved_bytes"] = n
        state["file_sha256"] = await asyncio.to_thread(digest_file, dest)
        return {"saved_bytes": n}
    finally:
        dest.unlink(missing_ok=True)


class Boundary:
    """Auth before parsing; bounded body; observe failure before cleanup."""

    async def __call__(self, scope, receive, send):
        global ACTIVE
        if scope["type"] != "http":
            return await app(scope, receive, send)
        if scope["path"] == "/healthz" and scope["method"] == "GET":
            return await JSONResponse({"ok": True, "source_sha": SOURCE},
                                      headers={"X-Upload-Probe": "1"})(scope, receive, send)
        headers = dict(scope["headers"])
        expected = ("Bearer " + TOKEN).encode()
        if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
            return await JSONResponse({"error": "unauthorized"}, 401,
                                      headers={"X-Upload-Probe": "1"})(scope, receive, send)
        if scope["path"] == "/status" and scope["method"] == "GET":
            return await JSONResponse(
                {"source_sha": SOURCE, "source_hashes": SOURCE_HASHES,
                 "filesystem": snapshot(), "durable_enabled": durable.enabled(),
                 "reserve_bytes": api.MIN_FREE, "max_upload_bytes": api.MAX_UPLOAD,
                 "parser_spool_max_size": formparsers.MultiPartParser.spool_max_size,
                 "versions": {p: importlib.metadata.version(p)
                              for p in ("fastapi", "starlette", "python-multipart", "uvicorn")}},
                headers={"X-Upload-Probe": "1"})(scope, receive, send)
        if scope["path"] != "/probe" or scope["method"] != "POST":
            return await JSONResponse({"error": "not_found"}, 404)(scope, receive, send)
        try:
            declared = int(headers[b"content-length"])
        except (KeyError, ValueError):
            return await JSONResponse({"error": "valid_content_length_required"}, 411)(scope, receive, send)
        if not 0 <= declared <= api.MAX_UPLOAD + (1 << 20):
            return await JSONResponse({"error": "probe_body_cap"}, 413)(scope, receive, send)
        if not GATE.acquire(blocking=False):
            return await JSONResponse({"error": "busy"}, 429)(scope, receive, send)
        state = {"phase": "parser", "before": snapshot(), "spools": [], "release_calls": 0}
        ACTIVE = state
        digest = hashlib.sha256()
        n = 0
        response_body = bytearray()
        status = 500
        errors = []
        peaks = {}
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.05):
                s = snapshot()
                for key in ("tmp", "data"):
                    peaks[key + "_min_free_bytes"] = min(
                        peaks.get(key + "_min_free_bytes", s[key]["free_bytes"]),
                        s[key]["free_bytes"])
                cg = s["cgroup"].get("memory.current", "")
                if cg.isdigit():
                    peaks["memory_current_peak_bytes"] = max(
                        peaks.get("memory_current_peak_bytes", 0), int(cg))

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        async def tap_receive():
            nonlocal n
            msg = await receive()
            if msg["type"] == "http.request":
                data = msg.get("body", b"")
                n += len(data)
                if n > api.MAX_UPLOAD + (1 << 20):
                    raise ValueError("probe_body_cap")
                digest.update(data)
            return msg

        async def capture(msg):
            nonlocal status
            if msg["type"] == "http.response.start":
                status = msg["status"]
            elif msg["type"] == "http.response.body":
                if len(response_body) + len(msg.get("body", b"")) > 65536:
                    raise RuntimeError("probe_response_cap")
                response_body.extend(msg.get("body", b""))

        old_trace = None
        # FastAPI may convert parser OSError into a generic 400. Capture its
        # errno at the parser thread even when the framework consumes it.
        def trace(frame, event, arg):
            if event == "exception" and frame.f_code.co_filename.endswith(("tempfile.py", "formparsers.py")):
                exc = arg[1]
                if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                    if "ENOSPC" not in errors:
                        errors.append("ENOSPC")
            return trace

        import sys
        old_trace = sys.gettrace()
        sys.settrace(trace)
        threading.settrace(trace)
        t0 = time.monotonic()
        try:
            try:
                await app(scope, tap_receive, capture)
            except Exception as exc:
                errors.append(type(exc).__name__)
                if isinstance(exc, OSError):
                    errors.append("errno=" + str(exc.errno))
            try:
                original = json.loads(response_body)
            except ValueError:
                original = {"response_body": response_body.decode("utf-8", "replace")[:500]}
            open_spools = [f for f in state["spools"] if not f.closed]
            state["production_open_spools_before_probe_cleanup"] = len(open_spools)
            state["part_exists_before_probe_cleanup"] = (api.DATA_DIR / "probe-upload.part").exists()
            state["after_production"] = snapshot()
            for spool in open_spools:
                spool.close()
            (api.DATA_DIR / "probe-upload.bin").unlink(missing_ok=True)
            (api.DATA_DIR / "probe-upload.part").unlink(missing_ok=True)
            state["after_cleanup"] = snapshot()
            result = {k: v for k, v in state.items() if k != "spools"}
            result.update(source_sha=SOURCE, source_hashes=SOURCE_HASHES,
                          versions={p: importlib.metadata.version(p)
                                    for p in ("fastapi", "starlette", "python-multipart", "uvicorn")},
                          durable_enabled=durable.enabled(), durable_flag_forced_test_only=True,
                          max_upload_bytes=api.MAX_UPLOAD, reserve_bytes=api.MIN_FREE,
                          received_bytes=n, sha256=digest.hexdigest(),
                          declared_body_bytes=declared, framing_seen="content-length",
                          production_status=status, production_response=original,
                          errors=errors, elapsed_s=round(time.monotonic() - t0, 3), sampled=peaks)
            await JSONResponse(result, status, headers={"X-Upload-Probe": "1"})(scope, receive, send)
        finally:
            sys.settrace(old_trace)
            threading.settrace(None)
            stop.set()
            thread.join(timeout=1)
            ACTIVE = None
            GATE.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(Boundary(), host="0.0.0.0", port=int(os.environ.get("PORT", "10000")),
                access_log=False, limit_concurrency=4, timeout_keep_alive=5)
