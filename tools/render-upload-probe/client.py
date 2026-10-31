"""Temporary Render upload-boundary probe client (test-only, stdlib only).

Streams deterministic synthetic bytes over HTTPS to the probe server and
verifies the server-reported length and SHA-256. Memory use is bounded by a
single 1 MiB block regardless of upload size.

The bearer secret is read only from the PROBE_TOKEN environment variable and
is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import select
import ssl
import sys
import time
from urllib.parse import urlsplit

MIB = 1024 * 1024
BLOCK = MIB
FIXTURE_BYTES = 230_400_044  # 20-minute 48 kHz/16-bit stereo WAV fixture
ORIGINAL_CAP_BYTES = 1024 * MIB  # original per-file audio limit
NAMED_SIZES = {"100mib": 100 * MIB, "fixture": FIXTURE_BYTES, "cap": ORIGINAL_CAP_BYTES}
BOUNDARY = "----chopLabUploadProbeBoundary7d3f1c"


class EarlyResponse(Exception):
    pass


def parse_size(text: str) -> int:
    key = text.strip().lower()
    if key in NAMED_SIZES:
        return NAMED_SIZES[key]
    m = re.fullmatch(r"(\d+)\s*(mib|gib|b)?", key)
    if not m:
        raise argparse.ArgumentTypeError(f"bad size: {text}")
    n = int(m.group(1))
    return n * {"mib": MIB, "gib": 1024 * MIB}.get(m.group(2) or "b", 1)


class Payload:
    """Deterministic byte stream: 1 MiB SHAKE-256 base block with the block
    index stamped into its first 8 bytes, optionally wrapped as multipart."""

    def __init__(self, file_bytes: int, multipart: bool, seed: bytes = b"choplab-upload-probe-v1"):
        self.file_bytes = file_bytes
        self.base = bytearray(hashlib.shake_256(seed).digest(BLOCK))
        if multipart:
            self.prefix = (
                f"--{BOUNDARY}\r\n"
                'Content-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode()
            self.suffix = f"\r\n--{BOUNDARY}--\r\n".encode()
        else:
            self.prefix = self.suffix = b""
        self.content_type = (f"multipart/form-data; boundary={BOUNDARY}" if multipart
                             else "application/octet-stream")
        self.total = len(self.prefix) + file_bytes + len(self.suffix)

    def chunks(self, start: int = 0, stop: int | None = None):
        """Yield body bytes in [start, stop) in pieces of at most 1 MiB."""
        stop = self.total if stop is None else stop
        pos = start
        p, f = len(self.prefix), self.file_bytes
        while pos < stop:
            if pos < p:
                piece = self.prefix[pos:min(p, stop)]
            elif pos < p + f:
                off = pos - p
                idx, inner = divmod(off, BLOCK)
                self.base[0:8] = idx.to_bytes(8, "big")
                end = min(BLOCK, inner + (stop - pos), f - idx * BLOCK)
                piece = bytes(self.base[inner:end])
            else:
                off = pos - p - f
                piece = self.suffix[off:off + (stop - pos)]
            pos += len(piece)
            yield piece


CAFILE = None


def _dpapi_decrypt(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(blob, len(blob))
    src = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                                    ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load_token(token_file: str | None, dpapi_file: str | None) -> str | None:
    """Secret sources: private file, Windows DPAPI blob file, or PROBE_TOKEN env.
    Never accepted on the command line and never printed."""
    if dpapi_file:
        with open(dpapi_file, "rb") as fh:
            raw = fh.read()
        if not raw.startswith(b"\x01\x00\x00\x00"):  # hex text (ConvertFrom-SecureString)
            raw = bytes.fromhex(raw.decode("ascii").strip())
        plain = _dpapi_decrypt(raw)
        text = plain.decode("utf-16-le") if b"\x00" in plain else plain.decode("utf-8")
        return text.strip().lstrip("\ufeff")
    if token_file:
        with open(token_file, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("PROBE_TOKEN")


def connect(parts, timeout: float):
    host, port = parts.hostname, parts.port
    if parts.scheme == "https":
        return http.client.HTTPSConnection(host, port or 443, timeout=timeout,
                                           context=ssl.create_default_context(cafile=CAFILE))
    return http.client.HTTPConnection(host, port or 80, timeout=timeout)


class _PrefixedRaw(io.RawIOBase):
    def __init__(self, prefix: bytes, sock):
        self.prefix, self.sock = prefix, sock

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        if self.prefix:
            n = min(len(buf), len(self.prefix))
            buf[:n] = self.prefix[:n]
            self.prefix = self.prefix[n:]
            return n
        return self.sock.recv_into(buf)


class _PrefixedSock:
    def __init__(self, prefix: bytes, sock):
        self.raw = _PrefixedRaw(prefix, sock)

    def makefile(self, *_a, **_k):
        return io.BufferedReader(self.raw)


def early_bytes(sock, timeout: float) -> bytes | None:
    """Return application bytes the peer sent before we finished the body.

    A bare select() is insufficient over TLS 1.3 (session tickets make the
    socket readable), so attempt a non-blocking read and ignore WANT_READ.
    """
    if not select.select([sock], [], [], 0)[0]:
        return None
    sock.setblocking(False)
    try:
        data = sock.recv(65536)
    except (ssl.SSLWantReadError, BlockingIOError):
        return None
    finally:
        sock.settimeout(timeout)
    if not data:
        raise ConnectionAbortedError("peer closed connection before body was complete")
    return data


def describe_response(resp) -> dict:
    raw = resp.read(64 * 1024)
    headers = {k.lower(): v for k, v in resp.getheaders()}
    info = {
        "status": resp.status,
        "from_app": headers.get("x-upload-probe") == "1",
        "edge_headers": {k: headers[k] for k in ("server", "rndr-id", "x-render-origin-server",
                                                 "cf-ray", "via") if k in headers},
    }
    try:
        info["body"] = json.loads(raw)
    except ValueError:
        info["body_excerpt"] = raw[:300].decode("utf-8", "replace")
    return info


def read_response(conn) -> dict:
    return describe_response(conn.getresponse())


def read_early_response(prefix: bytes, sock) -> dict:
    resp = http.client.HTTPResponse(_PrefixedSock(prefix, sock), method="POST")
    resp.begin()
    return describe_response(resp)


def get_json(parts, path: str, token: str | None, timeout: float) -> dict:
    conn = connect(parts, timeout)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        conn.request("GET", path, headers=headers)
        return read_response(conn)
    finally:
        conn.close()


def poll_progress(parts, token: str, upload_id: str, seconds: float, timeout: float) -> list:
    """Independent authenticated observations taken while the upload is paused."""
    observations = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            r = get_json(parts, f"/progress?id={upload_id}", token, timeout)
            body = r.get("body", {})
            observations.append({"t": round(time.time(), 3), "status": r["status"],
                                 "from_app": r["from_app"],
                                 "received_bytes": body.get("received_bytes"),
                                 "state": body.get("state")})
            if r["status"] == 200 and (body.get("received_bytes") or 0) > 0:
                break
        except (OSError, http.client.HTTPException) as exc:
            observations.append({"t": round(time.time(), 3), "error": type(exc).__name__})
        time.sleep(1.0)
    return observations


def run_probe(args, token: str | None) -> dict:
    parts = urlsplit(args.url)
    payload = Payload(args.size, args.multipart)
    upload_id = secrets.token_urlsafe(12)
    phase1 = 0
    if args.two_phase:
        phase1 = min(args.phase1_bytes, payload.total - 1)
    result = {
        "label": args.label or f"{args.size}B {'multipart' if args.multipart else 'raw'} {args.framing}"
                                f"{' two-phase' if args.two_phase else ''}",
        "url_host": parts.hostname,
        "framing_sent": args.framing,
        "multipart": args.multipart,
        "file_bytes": args.size,
        "body_bytes": payload.total,
        "multipart_overhead_bytes": payload.total - args.size,
        "upload_id": upload_id,
    }
    expected = hashlib.sha256()
    sent = 0
    t0 = time.monotonic()
    phase = "connect"
    conn = connect(parts, args.timeout)
    try:
        conn.putrequest("POST", "/probe", skip_accept_encoding=True)
        if token is not None:
            conn.putheader("Authorization", f"Bearer {token}")
        conn.putheader("Content-Type", payload.content_type)
        conn.putheader("X-Probe-Upload-Id", upload_id)
        if args.framing == "chunked":
            conn.putheader("Transfer-Encoding", "chunked")
        else:
            conn.putheader("Content-Length", str(payload.total))
        conn.endheaders()
        phase = "send"

        def send_range(start, stop):
            nonlocal sent
            for piece in payload.chunks(start, stop):
                early = early_bytes(conn.sock, args.timeout)
                if early is not None:
                    raise EarlyResponse(early)
                expected.update(piece)
                if args.framing == "chunked":
                    conn.send(b"%x\r\n" % len(piece) + piece + b"\r\n")
                else:
                    conn.send(piece)
                sent += len(piece)

        if phase1:
            send_range(0, phase1)
            phase = "paused"
            pause_started = time.time()
            obs = poll_progress(parts, token, upload_id, args.pause_s, args.timeout)
            seen = max((o.get("received_bytes") or 0 for o in obs), default=0)
            result["two_phase"] = {
                "phase1_bytes_sent": phase1,
                "bytes_withheld_during_pause": payload.total - phase1,
                "pause_started_at": round(pause_started, 3),
                "max_server_received_during_pause": seen,
                "observations": obs,
                "streaming": "proven" if seen > 0 else "inconclusive",
            }
            phase = "send"
        send_range(phase1, payload.total)
        if args.framing == "chunked":
            conn.send(b"0\r\n\r\n")
        result["send_s"] = round(time.monotonic() - t0, 3)
        phase = "response"
        resp = read_response(conn)
    except EarlyResponse as early:
        # Peer answered before the body was complete (rejection or edge error).
        result["early_response"] = True
        phase = "response"
        try:
            resp = read_early_response(early.args[0], conn.sock)
        except (OSError, http.client.HTTPException) as exc:
            resp = None
            err = exc
    except (OSError, http.client.HTTPException) as exc:
        if phase == "connect":
            # DNS/TCP/TLS failure before any body byte: local network or wrong URL.
            result.update(outcome="client_or_network_failure", detail=f"{type(exc).__name__}: {exc}")
            conn.close()
            return result
        resp = None
        err = exc

    result["bytes_sent"] = sent
    result["elapsed_s"] = round(time.monotonic() - t0, 3)
    if resp is None:
        # Connection broke mid-request; try to salvage an early response, then
        # ask the app (out of band) what it actually received.
        try:
            resp = read_response(conn)
        except (OSError, http.client.HTTPException):
            resp = None
        if resp is None:
            result["error"] = f"{type(err).__name__}: {err}"
            result["phase"] = phase
            try:
                result["server_progress_after"] = get_json(
                    parts, f"/progress?id={upload_id}", token, args.timeout).get("body")
            except (OSError, http.client.HTTPException) as exc2:
                result["server_progress_after"] = f"unavailable: {type(exc2).__name__}"
            result["outcome"] = "connection_aborted"
            conn.close()
            return result
    conn.close()

    result["response"] = resp
    body = resp.get("body") or {}
    if not resp["from_app"]:
        result["outcome"] = "platform_rejected"
    elif resp["status"] != 200:
        result["outcome"] = "application_rejected"
    elif body.get("received_bytes") == payload.total and body.get("sha256") == expected.hexdigest():
        result["outcome"] = "ok"
    else:
        result["outcome"] = "integrity_mismatch"
    result["expected_sha256"] = expected.hexdigest()
    if args.framing == "content-length" and body.get("framing_seen"):
        result["edge_reframed"] = body.get("framing_seen") != args.framing
    elif body.get("framing_seen"):
        result["edge_reframed"] = body.get("framing_seen") != "chunked"
    if args.expect_status is not None:
        result["matched_expectation"] = (resp["status"] == args.expect_status
                                         and resp["from_app"] == (args.expect_from != "platform"))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="probe base URL, e.g. https://x.onrender.com")
    ap.add_argument("--size", type=parse_size, default=100 * MIB,
                    help="file bytes: 100mib | fixture | cap | <n>[MiB|GiB]")
    ap.add_argument("--framing", choices=["content-length", "chunked"], default="content-length")
    ap.add_argument("--multipart", action="store_true", help="wrap file bytes in multipart/form-data")
    ap.add_argument("--two-phase", action="store_true",
                    help="send phase-1 bytes, pause, and observe server progress out of band")
    ap.add_argument("--phase1-bytes", type=parse_size, default=8 * MIB)
    ap.add_argument("--pause-s", type=float, default=20.0)
    ap.add_argument("--timeout", type=float, default=120.0, help="per-socket-operation timeout")
    ap.add_argument("--no-auth", action="store_true", help="omit the token (negative test)")
    ap.add_argument("--expect-status", type=int)
    ap.add_argument("--expect-from", choices=["app", "platform"], default="app")
    ap.add_argument("--label")
    ap.add_argument("--allow-http", action="store_true", help="permit http:// (local/CI only)")
    ap.add_argument("--cafile", help="extra CA bundle (CI self-signed proxy only)")
    ap.add_argument("--token-file", help="private file containing the token (else PROBE_TOKEN env)")
    ap.add_argument("--token-dpapi-file", help="Windows DPAPI (CurrentUser) protected token file")
    ap.add_argument("--health", action="store_true", help="only GET /healthz")
    ap.add_argument("--dry-run", action="store_true", help="compute expected length/digest only")
    args = ap.parse_args()

    global CAFILE
    CAFILE = args.cafile
    parts = urlsplit(args.url)
    if parts.scheme != "https" and not (args.allow_http and parts.scheme == "http"):
        print(json.dumps({"outcome": "client_failure", "detail": "https:// URL required"}))
        return 2

    if args.dry_run:
        p = Payload(args.size, args.multipart)
        h = hashlib.sha256()
        for piece in p.chunks():
            h.update(piece)
        print(json.dumps({"body_bytes": p.total, "sha256": h.hexdigest()}))
        return 0

    if args.health:
        try:
            r = get_json(parts, "/healthz", None, args.timeout)
        except (OSError, http.client.HTTPException) as exc:
            print(json.dumps({"outcome": "client_or_network_failure", "detail": type(exc).__name__}))
            return 1
        print(json.dumps(r, indent=2))
        return 0 if r["status"] == 200 and r["from_app"] else 1

    token = None
    if not args.no_auth:
        try:
            token = load_token(args.token_file, args.token_dpapi_file)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            print(json.dumps({"outcome": "client_failure",
                              "detail": f"could not load token: {type(exc).__name__}"}))
            return 2
        if not token:
            print(json.dumps({"outcome": "client_failure",
                              "detail": "no token (PROBE_TOKEN, --token-file or --token-dpapi-file)"}))
            return 2
    elif args.two_phase:
        print(json.dumps({"outcome": "client_failure", "detail": "--two-phase needs auth"}))
        return 2

    result = run_probe(args, token)
    print(json.dumps(result, indent=2))
    if args.expect_status is not None:
        return 0 if result.get("matched_expectation") else 1
    return 0 if result.get("outcome") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
