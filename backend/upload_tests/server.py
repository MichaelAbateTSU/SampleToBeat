"""Synthetic-only parser/save measurement harness, never a product deployment."""
import hashlib
import os
from pathlib import Path
import secrets

token = os.environ["TEST_TOKEN"]
salt = secrets.token_bytes(16)
secret = Path("/tmp/test-upload-password")
secret.write_text("pbkdf2_sha256$600000$" + salt.hex() + "$"
                  + hashlib.pbkdf2_hmac("sha256", token.encode(), salt, 600000).hex())
secret.chmod(0o600)
os.environ["CHOPLAB_PASSWORD_HASH_FILE"] = str(secret)

import asyncio
import json
import resource
import shutil
import threading
import time

from choplab import api, durable
from choplab.hosting import OwnerAccess
from choplab.object_storage import _release_file_cache
from choplab.uploads import upload_route
from fastapi import FastAPI, File, UploadFile
from starlette.requests import Request

app = FastAPI()
if os.environ.get("CACHE_MODE") == "bounded":
    app.router.route_class = upload_route(api.MAX_UPLOAD)
app.add_middleware(OwnerAccess)
durable._settings = object()  # Only the real enabled() predicate; no remote configuration.


def metrics():
    values = {}
    for name in ("memory.current", "memory.peak", "memory.max", "memory.stat", "memory.events"):
        p = Path("/sys/fs/cgroup") / name
        if p.exists():
            text = p.read_text().strip()
            values[name] = dict(line.split() for line in text.splitlines()) if " " in text else int(text)
    values["rss_max_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    values["tmp_free"] = shutil.disk_usage("/tmp").free
    values["data_free"] = shutil.disk_usage("/data").free
    return values


@app.get("/api/health")
def health():
    return api.health()


@app.get("/api/test/status")
def status():
    return {"mode": os.environ.get("CACHE_MODE"), "metrics": metrics(),
            "remaining_data_files": [p.name for p in Path("/data").iterdir()]}


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
            _release_file_cache(f)
    return digest.hexdigest()


@app.post("/api/test/upload")
async def upload(file: UploadFile = File(...)):
    after_parse = metrics()
    path = Path("/data/upload-test.bin")
    try:
        n = await api._save_upload(file, path)
        after_save = metrics()
        digest = await asyncio.to_thread(file_digest, path)
        return {"saved_bytes": n, "file_sha256": digest,
                "after_parse": after_parse, "after_save": after_save,
                "spool_rolled": getattr(file.file, "_rolled", None)}
    finally:
        path.unlink(missing_ok=True)


class Instrumented:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/test/upload":
            return await app(scope, receive, send)
        digest, total = hashlib.sha256(), 0
        samples = []
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.25):
                samples.append({"at": time.monotonic(), **metrics()})
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        start = time.monotonic()
        chunks, status = [], 500

        async def tap():
            nonlocal total
            msg = await receive()
            data = msg.get("body", b"")
            digest.update(data)
            total += len(data)
            return msg

        async def capture(msg):
            nonlocal status
            if msg["type"] == "http.response.start":
                status = msg["status"]
            elif msg["type"] == "http.response.body":
                chunks.append(msg.get("body", b""))
        try:
            await app(scope, tap, capture)
            from starlette.responses import JSONResponse
            result = json.loads(b"".join(chunks))
            result.update(received_bytes=total, body_sha256=digest.hexdigest(),
                          elapsed_s=time.monotonic() - start, after_request=metrics(),
                          samples=samples, status=status)
            await JSONResponse(result, status)(scope, receive, send)
        finally:
            stop.set()
            thread.join(timeout=1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(Instrumented(), host="0.0.0.0", port=10000, access_log=False,
                forwarded_allow_ips="*", timeout_keep_alive=5)
