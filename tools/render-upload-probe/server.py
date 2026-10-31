"""Temporary Render upload-boundary probe server (test-only, stdlib only).

Streams each request body through SHA-256 in fixed-size chunks and discards it.
Nothing is written to disk and the secret is never logged.

Endpoints:
  GET  /healthz                 unauthenticated liveness
  POST /probe                   authenticated; body is hashed and discarded
  GET  /progress?id=<upload-id> authenticated; bytes received so far for an upload
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    import resource
except ImportError:  # Windows local runs only
    resource = None

CHUNK = 1024 * 1024
TOKEN = os.environ.get("PROBE_TOKEN", "")
MAX_BYTES = int(os.environ.get("PROBE_MAX_BYTES", str(1200 * 1024 * 1024)))
MAX_CONCURRENT = int(os.environ.get("PROBE_MAX_CONCURRENT", "1"))
READ_TIMEOUT = float(os.environ.get("PROBE_READ_TIMEOUT_S", "120"))
UPLOAD_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)
UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

PROGRESS_LOCK = threading.Lock()
PROGRESS: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
PROGRESS_KEEP = 16


def set_progress(upload_id: str | None, **fields) -> None:
    if not upload_id:
        return
    with PROGRESS_LOCK:
        entry = PROGRESS.setdefault(upload_id, {})
        entry.update(fields)
        PROGRESS.move_to_end(upload_id)
        while len(PROGRESS) > PROGRESS_KEEP:
            PROGRESS.popitem(last=False)


class BodyTooLarge(Exception):
    pass


class BadFraming(Exception):
    pass


def max_rss_kib() -> int | None:
    if resource is None:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "upload-probe"
    sys_version = ""
    timeout = READ_TIMEOUT

    def log_message(self, fmt: str, *args) -> None:
        # Request line/status only; never headers (Authorization) or bodies.
        sys.stderr.write("%s\n" % (fmt % args))

    def handle_expect_100(self) -> bool:
        # Reject before the client streams a body when auth or size is wrong.
        problem = self._precheck()
        if problem:
            self._json(*problem, close=True)
            return False
        return super().handle_expect_100()

    def _json(self, status: int, payload: dict, close: bool = False) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Upload-Probe", "1")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)
        if close:
            self._linger()

    def _linger(self) -> None:
        # Half-close and drain a bounded amount so the client can read the
        # rejection before a TCP reset discards it.
        try:
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + 2.0
            drained = 0
            while drained < 8 * CHUNK and time.monotonic() < deadline:
                self.connection.settimeout(max(0.05, deadline - time.monotonic()))
                data = self.connection.recv(65536)
                if not data:
                    break
                drained += len(data)
        except OSError:
            pass

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not TOKEN or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].encode(), TOKEN.encode())

    def _upload_id(self) -> str | None:
        value = self.headers.get("X-Probe-Upload-Id")
        return value if value and UPLOAD_ID_RE.match(value) else None

    def _precheck(self):
        if urlsplit(self.path).path != "/probe" or self.command != "POST":
            return 404, {"error": "not_found"}
        if not self._authorized():
            return 401, {"error": "unauthorized"}
        te = self.headers.get("Transfer-Encoding")
        cl = self.headers.get("Content-Length")
        if te and te.strip().lower() != "chunked":
            return 501, {"error": "unsupported_transfer_encoding"}
        if not te:
            if cl is None:
                return 411, {"error": "length_required"}
            try:
                declared = int(cl)
            except ValueError:
                return 400, {"error": "bad_content_length"}
            if declared < 0:
                return 400, {"error": "bad_content_length"}
            if declared > MAX_BYTES:
                return 413, {"error": "declared_length_exceeds_max", "max_bytes": MAX_BYTES,
                             "declared_length": declared}
        return None

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/healthz":
            self._json(200, {"ok": True, "max_bytes": MAX_BYTES})
        elif parts.path == "/progress":
            if not self._authorized():
                self._json(401, {"error": "unauthorized"}, close=True)
                return
            upload_id = (parse_qs(parts.query).get("id") or [""])[0]
            with PROGRESS_LOCK:
                entry = dict(PROGRESS.get(upload_id, {}))
            if not entry:
                self._json(404, {"error": "unknown_upload_id", "observed_at": time.time()})
            else:
                entry["observed_at"] = time.time()
                self._json(200, entry)
        else:
            self._json(404, {"error": "not_found"}, close=True)

    def do_POST(self) -> None:
        problem = self._precheck()
        if problem:
            self._json(*problem, close=True)
            return
        if not UPLOAD_SLOTS.acquire(blocking=False):
            self._json(429, {"error": "busy", "max_concurrent": MAX_CONCURRENT}, close=True)
            return
        try:
            self._consume()
        finally:
            UPLOAD_SLOTS.release()

    def _consume(self) -> None:
        te = self.headers.get("Transfer-Encoding")
        cl = self.headers.get("Content-Length")
        framing = "chunked" if te else "content-length"
        upload_id = self._upload_id()
        digest = hashlib.sha256()
        received = 0
        t0 = time.monotonic()
        first_byte_s = None
        max_gap_s = 0.0
        last = t0
        last_progress = 0.0
        set_progress(upload_id, state="receiving", received_bytes=0, framing_seen=framing,
                     headers_received_at=time.time())

        def feed(data: bytes) -> None:
            nonlocal received, first_byte_s, max_gap_s, last, last_progress
            now = time.monotonic()
            if first_byte_s is None and data:
                first_byte_s = now - t0
            max_gap_s = max(max_gap_s, now - last)
            last = now
            received += len(data)
            if received > MAX_BYTES:
                raise BodyTooLarge()
            digest.update(data)
            if now - last_progress >= 0.2:
                last_progress = now
                set_progress(upload_id, received_bytes=received)

        try:
            if framing == "content-length":
                remaining = int(cl)
                while remaining:
                    data = self.rfile.read1(min(CHUNK, remaining))
                    if not data:
                        raise BadFraming("eof_before_content_length")
                    remaining -= len(data)
                    feed(data)
            else:
                self._read_chunked(feed)
        except BodyTooLarge:
            set_progress(upload_id, state="rejected_too_large", received_bytes=received)
            self._json(413, {"error": "body_exceeds_max", "max_bytes": MAX_BYTES,
                             "received_at_abort": received, "framing_seen": framing}, close=True)
            return
        except BadFraming as exc:
            set_progress(upload_id, state="bad_framing", received_bytes=received)
            self._json(400, {"error": "bad_framing", "detail": str(exc),
                             "received_at_abort": received, "framing_seen": framing}, close=True)
            return
        except (TimeoutError, OSError) as exc:
            set_progress(upload_id, state="aborted", received_bytes=received)
            self.log_message("POST /probe aborted after %d bytes: %s", received, type(exc).__name__)
            self.close_connection = True
            return

        set_progress(upload_id, state="complete", received_bytes=received)
        self._json(200, {
            "received_bytes": received,
            "sha256": digest.hexdigest(),
            "framing_seen": framing,
            "content_length_seen": int(cl) if cl is not None and not te else None,
            "first_body_byte_s": round(first_byte_s, 3) if first_byte_s is not None else None,
            "body_read_s": round(time.monotonic() - t0, 3),
            "max_inter_chunk_gap_s": round(max_gap_s, 3),
            "server_max_rss_kib": max_rss_kib(),
            "max_bytes": MAX_BYTES,
        })

    def _read_chunked(self, feed) -> None:
        while True:
            line = self.rfile.readline(1024)
            if not line.endswith(b"\n"):
                raise BadFraming("bad_chunk_size_line")
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                raise BadFraming("bad_chunk_size")
            if size == 0:
                for _ in range(64):  # bounded trailers
                    trailer = self.rfile.readline(8192)
                    if trailer in (b"\r\n", b"\n", b""):
                        return
                raise BadFraming("too_many_trailers")
            remaining = size
            while remaining:
                data = self.rfile.read1(min(CHUNK, remaining))
                if not data:
                    raise BadFraming("eof_in_chunk")
                remaining -= len(data)
                feed(data)
            if self.rfile.readline(3) not in (b"\r\n", b"\n"):
                raise BadFraming("missing_chunk_crlf")


def main() -> None:
    if len(TOKEN) < 32:
        sys.exit("PROBE_TOKEN must be set to a random secret of at least 32 characters")
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    sys.stderr.write(f"upload-probe listening on 0.0.0.0:{port} max_bytes={MAX_BYTES}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
