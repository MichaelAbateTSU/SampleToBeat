"""Opt-in valid-audio test for the real source endpoint. Dry-run by default."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import time
from urllib.parse import quote, urlsplit

from fixture import FILE_BYTES, FRAMES, RATE, chunks, describe

BOUNDARY = "----chopLabUploadProbeBoundary7d3f1c"
PREFIX = (f"--{BOUNDARY}\r\n"
          'Content-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
          "Content-Type: audio/wav\r\n\r\n").encode()
SUFFIX = f"\r\n--{BOUNDARY}--\r\n".encode()
BODY_BYTES = len(PREFIX) + FILE_BYTES + len(SUFFIX)
INTERNAL_FRAMES = FRAMES // 2
INTERNAL_PCM_BYTES = INTERNAL_FRAMES * 2 * 4
RESERVE_BYTES = 2 << 30
MIN_LOCAL_BYTES = 5 << 30
MIN_OBJECT_BYTES = FILE_BYTES + INTERNAL_PCM_BYTES + 4096
MAX_RESPONSE_BYTES = 1 << 20
TERMINAL = {"completed", "failed", "canceled", "interrupted"}


def plan():
    return {**describe(), "multipart_body_bytes": BODY_BYTES,
            "internal_stereo_48khz_frames": INTERNAL_FRAMES,
            "internal_float32_pcm_bytes": INTERNAL_PCM_BYTES,
            "input_ingress_lower_bound_bytes": BODY_BYTES,
            "durable_object_storage_lower_bound_bytes": FILE_BYTES + INTERNAL_PCM_BYTES,
            "durable_publish_egress_lower_bound_bytes": FILE_BYTES + INTERNAL_PCM_BYTES,
            "approved_local_available_required_bytes": MIN_LOCAL_BYTES,
            "warning": "No integrated/provider success; job/analysis adds unknown storage and egress."}


def validate_budget(budget, origin, final_sha, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not re.fullmatch(r"[0-9a-f]{40}", final_sha):
        raise ValueError("supply the exact final deployed source SHA")
    if budget.get("origin") != origin or budget.get("final_source_sha") != final_sha:
        raise ValueError("budget origin/source must match final deployment")
    if budget.get("approved") is not True:
        raise ValueError("explicit measured quota approval required")
    expires = datetime.fromisoformat(budget["expires_at"].replace("Z", "+00:00"))
    measured = datetime.fromisoformat(budget["measured_at"].replace("Z", "+00:00"))
    if expires.tzinfo is None or measured.tzinfo is None:
        raise ValueError("budget timestamps must have timezones")
    if expires <= now or not 0 <= (now - measured).total_seconds() <= 900:
        raise ValueError("budget expired or capacity/quota measurement older than 15 minutes")
    for key, minimum in (
        ("input_ingress_bytes", BODY_BYTES), ("available_local_bytes", MIN_LOCAL_BYTES),
        ("tmp_available_bytes", FILE_BYTES + (8 << 20)),
        ("data_available_bytes", RESERVE_BYTES + FILE_BYTES + INTERNAL_PCM_BYTES + (64 << 20)),
        ("object_storage_available_bytes", MIN_OBJECT_BYTES),
        ("app_egress_available_bytes", MIN_OBJECT_BYTES), ("max_http_requests", 260),
    ):
        value = budget.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"insufficient measured approval: {key} requires >= {minimum}")
    if not budget.get("measurement_evidence") or not budget.get("analysis_extra_budget_acknowledged"):
        raise ValueError("quota evidence and additional analysis-budget acknowledgement required")
    if budget.get("max_upload_mb") != 1024 or budget.get("max_audio_seconds", 0) < FRAMES / RATE:
        raise ValueError("final app upload/duration cap cannot accept fixture")
    return budget


def private_password(args):
    if args.password_dpapi_file:
        import ctypes
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]
        raw = Path(args.password_dpapi_file).read_bytes()
        if not raw.startswith(b"\x01\x00\x00\x00"):
            raw = bytes.fromhex(raw.decode("ascii").strip())
        buf = ctypes.create_string_buffer(raw, len(raw))
        src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        dest = Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dest)
        ):
            raise OSError("DPAPI decryption failed")
        try:
            value = ctypes.string_at(dest.data, dest.size)
            return (value.decode("utf-16-le") if b"\x00" in value else value.decode("utf-8")).strip()
        finally:
            ctypes.windll.kernel32.LocalFree(dest.data)
    if args.password_file:
        return Path(args.password_file).read_text(encoding="utf-8").strip()
    return os.environ.get("CHOPLAB_TEST_PASSWORD", "")


class Transport:
    def __init__(self, origin, username, password, budget, *, allow_loopback=False):
        self.origin, self.parts, self.budget = origin, urlsplit(origin), budget
        self.calls = 0
        self.password = password
        if self.parts.username or self.parts.password or self.parts.path not in ("", "/") or self.parts.query or self.parts.fragment:
            raise ValueError("origin only, no embedded credentials, path or query")
        if self.parts.scheme != "https" and not (
            allow_loopback and self.parts.scheme == "http" and self.parts.hostname in ("localhost", "127.0.0.1")
        ):
            raise ValueError("HTTPS required (HTTP permitted only for explicit offline loopback tests)")
        self.auth = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()

    def connect(self):
        if self.calls >= self.budget["max_http_requests"]:
            raise RuntimeError("approved HTTP request budget exhausted")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.budget["expires_at"].replace("Z", "+00:00")):
            raise RuntimeError("approved quota budget expired")
        self.calls += 1
        if self.parts.scheme == "https":
            return http.client.HTTPSConnection(
                self.parts.hostname, self.parts.port or 443,
                timeout=180, context=ssl.create_default_context())
        return http.client.HTTPConnection(self.parts.hostname, self.parts.port or 80, timeout=180)

    def read(self, conn):
        response = conn.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("response exceeded cap")
        result = {"status": response.status}
        if self.password and self.password.encode() in raw:
            raise RuntimeError("refusing credential-bearing response")
        try:
            result["body"] = json.loads(raw) if raw else {}
        except ValueError:
            result["non_json_response"] = True
        return result

    def request(self, method, path, body=None):
        conn = self.connect()
        try:
            encoded = json.dumps(body).encode() if body is not None else None
            headers = {"Authorization": self.auth, "Content-Type": "application/json"}
            conn.request(method, path, encoded, headers)
            return self.read(conn)
        finally:
            conn.close()

    def upload(self, project_id, expected_file_digest):
        conn = self.connect()
        file_hash, body_hash = hashlib.sha256(), hashlib.sha256()
        sent = 0
        try:
            conn.putrequest("POST", f"/api/projects/{quote(project_id, safe='')}/sources")
            conn.putheader("Authorization", self.auth)
            conn.putheader("Content-Length", str(BODY_BYTES))
            conn.putheader("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
            conn.endheaders()
            conn.send(PREFIX)
            body_hash.update(PREFIX)
            sent += len(PREFIX)
            for piece in chunks():
                conn.send(piece)
                file_hash.update(piece)
                body_hash.update(piece)
                sent += len(piece)
            conn.send(SUFFIX)
            body_hash.update(SUFFIX)
            sent += len(SUFFIX)
            response = self.read(conn)
            if file_hash.hexdigest() != expected_file_digest:
                raise RuntimeError("deterministic fixture changed between hash and send")
            return {**response, "sent_body_bytes": sent,
                    "sent_file_sha256": file_hash.hexdigest(),
                    "sent_multipart_sha256": body_hash.hexdigest()}
        finally:
            conn.close()


def require(response, code):
    if response["status"] != code:
        raise RuntimeError(f"HTTP {response['status']} (expected {code}); no retry")
    return response["body"]


def cleanup_owned(transport, state):
    project_id = state.get("project_id")
    if not project_id:
        return {"outcome": "unknown_create_retained", "name": state["project_name"]}
    project_path = "/api/projects/" + quote(project_id, safe="")
    project = transport.request("GET", project_path)
    if project["status"] == 404:
        return {"outcome": "already_absent"}
    record = require(project, 200)
    if record.get("id") != project_id or record.get("name") != state["project_name"]:
        return {"outcome": "identity_mismatch_retained"}
    jobs = require(transport.request("GET", project_path + "/jobs"), 200)
    if not isinstance(jobs, list) or any(job.get("status") not in TERMINAL for job in jobs):
        return {"outcome": "active_or_unknown_jobs_retained", "project_id": project_id}
    deletion = transport.request("DELETE", project_path)
    if deletion["status"] != 204:
        return {"outcome": "delete_rejected_retained", "status": deletion["status"]}
    check = transport.request("GET", project_path)
    return {"outcome": "metadata_removed" if check["status"] == 404 else "delete_unverified",
            "object_cleanup": "not established by metadata 404; verify durable outbox/object inventory"}


def execute(transport, state, persist, expected_digest, *, max_polls=120, sleep=time.sleep):
    """Never retry writes; journal identities before upload and retain unknown jobs."""
    try:
        capabilities = require(transport.request("GET", "/api/system/capabilities"), 200)
        if capabilities.get("max_upload_mb") != 1024 or capabilities.get("max_audio_seconds", 0) < FRAMES / RATE:
            raise RuntimeError("actual app caps differ from approved fixture preflight")
        # Coarse public capability value cannot replace measured filesystem budget.
        if capabilities.get("disk_free_gb", 0) * 1e9 < MIN_LOCAL_BYTES:
            raise RuntimeError("app reports insufficient local disk space")
        state["create_attempted"] = True
        with args.out.open("x", encoding="utf-8") as receipt:
            receipt.write(json.dumps(state, indent=2))
        created = require(transport.request("POST", "/api/projects", {"name": state["project_name"]}), 201)
        if not isinstance(created.get("id"), str) or not created["id"]:
            raise RuntimeError("create returned no project identity")
        state["project_id"] = created["id"]
        persist()
        state["upload_attempted"] = True
        persist()
        upload = transport.upload(state["project_id"], expected_digest)
        state["upload_status"] = upload["status"]
        state.update({k: v for k, v in upload.items() if k.startswith("sent_")})
        result = require(upload, 202)
        state["source_id"], state["job_id"] = result["source"]["id"], result["job"]["id"]
        state["outcome"] = "accepted_not_completed"
        persist()
        for _ in range(max_polls):
            job = require(transport.request("GET", "/api/jobs/" + quote(state["job_id"], safe="")), 200)
            state["job_status"] = job.get("status")
            if state["job_status"] in TERMINAL:
                source = require(transport.request("GET", "/api/sources/" + quote(state["source_id"], safe="")), 200)
                state["server_original_sha256"] = source.get("content_hash")
                state["metadata"] = source.get("metadata")
                state["duration_seconds"] = source.get("duration_seconds")
                state["outcome"] = "verified_import" if (
                    state["job_status"] == "completed"
                    and source.get("content_hash") == expected_digest
                    and source.get("metadata", {}).get("sample_rate") == RATE
                    and source.get("metadata", {}).get("channels") == 4
                    and abs(source.get("duration_seconds", 0) - FRAMES / RATE) < 0.01
                ) else "terminal_job_not_verified"
                break
            sleep(15)
        else:
            state["outcome"] = "poll_limit_unknown_retained"
    except (OSError, http.client.HTTPException, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # Do not serialize arbitrary SDK/network exception text containing credentials.
        state["outcome"] = "failed_or_unknown"
        state["error_class"] = type(exc).__name__
    finally:
        if state.get("create_attempted"):
            try:
                state["cleanup"] = cleanup_owned(transport, state)
            except (OSError, http.client.HTTPException, RuntimeError, ValueError, KeyError, TypeError) as exc:
                state["cleanup"] = {"outcome": "cleanup_unknown_retained", "error_class": type(exc).__name__}
        state["http_requests_used"] = transport.calls
        persist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--origin")
    parser.add_argument("--final-source-sha")
    parser.add_argument("--password-file")
    parser.add_argument("--password-dpapi-file")
    parser.add_argument("--username", default="owner")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(plan(), indent=2))
        return 0
    if not all((args.budget, args.origin, args.final_source_sha, args.out)):
        parser.error("execution requires budget, final origin/source SHA and private receipt output")
    budget = validate_budget(json.loads(args.budget.read_text()), args.origin, args.final_source_sha)
    password = private_password(args)
    if not password:
        parser.error("private password source required")
    if args.out.exists():
        parser.error("receipt already exists; refusing to repeat an unknown prior upload")
    digest = hashlib.sha256()
    for piece in chunks():
        digest.update(piece)
    state = {"plan": plan(), "origin": args.origin, "final_source_sha": args.final_source_sha,
             "expected_file_sha256": digest.hexdigest(),
             "project_name": "valid-gib-preflight-" + secrets.token_hex(16),
             "outcome": "not_started"}
    def persist():
        text = json.dumps(state, indent=2)
        if password in text:
            raise RuntimeError("refusing credential-bearing receipt")
        args.out.write_text(text, encoding="utf-8")
    persist()
    transport = Transport(args.origin, args.username, password, budget)
    execute(transport, state, persist, digest.hexdigest())
    print(json.dumps({"outcome": state["outcome"], "cleanup": state.get("cleanup")}, indent=2))
    return 0 if state["outcome"] == "verified_import" else 1


if __name__ == "__main__":
    raise SystemExit(main())
