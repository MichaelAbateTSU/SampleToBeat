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


class PreflightError(RuntimeError):
    def __init__(self, reason, status=None):
        super().__init__(reason)
        self.reason, self.status = reason, status


def plan(fixture_module=None):
    details = describe() if fixture_module is None else fixture_module.describe()
    internal_frames = details["frames"] // 2
    internal_pcm = internal_frames * 8
    return {**details, "multipart_body_bytes": BODY_BYTES,
            "internal_stereo_48khz_frames": internal_frames,
            "internal_float32_pcm_bytes": internal_pcm,
            "input_ingress_lower_bound_bytes": BODY_BYTES,
            "durable_object_storage_lower_bound_bytes": FILE_BYTES + internal_pcm,
            "durable_publish_egress_lower_bound_bytes": FILE_BYTES + internal_pcm,
            "approved_local_available_required_bytes": MIN_LOCAL_BYTES,
            "warning": "No integrated/provider success; job/analysis adds unknown storage and egress."}


def validate_budget(budget, origin, final_sha, *, now=None, cleanup_only=False, fixture_module=None):
    now = now or datetime.now(timezone.utc)
    details = plan(fixture_module)
    internal_pcm = details["internal_float32_pcm_bytes"]
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
    requirements = (("max_http_requests", 10),) if cleanup_only else (
        ("input_ingress_bytes", BODY_BYTES), ("available_local_bytes", MIN_LOCAL_BYTES),
        ("tmp_available_bytes", FILE_BYTES + (8 << 20)),
        ("data_available_bytes", RESERVE_BYTES + FILE_BYTES + internal_pcm + (64 << 20)),
        ("object_storage_available_bytes", FILE_BYTES + internal_pcm + 4096),
        ("app_egress_available_bytes", FILE_BYTES + internal_pcm + 4096), ("max_http_requests", 260),
    )
    for key, minimum in requirements:
        value = budget.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"insufficient measured approval: {key} requires >= {minimum}")
    if not budget.get("measurement_evidence"):
        raise ValueError("measured quota evidence required")
    if not cleanup_only and budget.get("analysis_extra_budget_acknowledged") is not True:
        raise ValueError("quota evidence and additional analysis-budget acknowledgement required")
    if not cleanup_only and (budget.get("max_upload_mb") != 1024 or budget.get("max_audio_seconds", 0) < details["duration_seconds"]):
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
    def __init__(self, origin, username, password, budget, *, allow_loopback=False, fixture_module=None):
        self.origin, self.parts, self.budget = origin, urlsplit(origin), budget
        self.calls = 0
        self.password = password
        self.fixture_module = fixture_module
        if self.parts.username or self.parts.password or self.parts.path not in ("", "/") or self.parts.query or self.parts.fragment:
            raise ValueError("origin only, no embedded credentials, path or query")
        if self.parts.scheme != "https" and not (
            allow_loopback and self.parts.scheme == "http" and self.parts.hostname in ("localhost", "127.0.0.1")
        ):
            raise ValueError("HTTPS required (HTTP permitted only for explicit offline loopback tests)")
        self.auth = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()

    def connect(self):
        if self.calls >= self.budget["max_http_requests"]:
            raise PreflightError("approved_request_budget_exhausted")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.budget["expires_at"].replace("Z", "+00:00")):
            raise PreflightError("approved_quota_budget_expired")
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
            raise PreflightError("response_cap_exceeded")
        result = {"status": response.status}
        if self.password and (self.password.encode() in raw or self.auth.encode() in raw):
            raise PreflightError("credential_bearing_response_blocked")
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
        file_bytes = BODY_BYTES - len(PREFIX) - len(SUFFIX)
        try:
            conn.putrequest("POST", f"/api/projects/{quote(project_id, safe='')}/sources")
            conn.putheader("Authorization", self.auth)
            conn.putheader("Content-Length", str(BODY_BYTES))
            conn.putheader("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
            conn.endheaders()
            conn.send(PREFIX)
            body_hash.update(PREFIX)
            sent += len(PREFIX)
            for piece in (chunks() if self.fixture_module is None else self.fixture_module.chunks()):
                if sent - len(PREFIX) + len(piece) > file_bytes:
                    raise PreflightError("fixture_exceeds_declared_content_length")
                conn.send(piece)
                file_hash.update(piece)
                body_hash.update(piece)
                sent += len(piece)
            if sent - len(PREFIX) != file_bytes:
                raise PreflightError("fixture_shorter_than_declared_content_length")
            conn.send(SUFFIX)
            body_hash.update(SUFFIX)
            sent += len(SUFFIX)
            response = self.read(conn)
            if file_hash.hexdigest() != expected_file_digest:
                raise PreflightError("fixture_digest_changed")
            return {**response, "sent_body_bytes": sent,
                    "sent_file_sha256": file_hash.hexdigest(),
                    "sent_multipart_sha256": body_hash.hexdigest()}
        finally:
            conn.close()


def require(response, code):
    if response["status"] != code:
        raise PreflightError("unexpected_http_response_no_retry", response["status"])
    return response["body"]


def cleanup_owned(transport, state):
    project_id = state.get("project_id")
    if not project_id:
        return {"outcome": "unknown_create_retained", "name": state["project_name"]}
    if state.get("upload_attempted") and state.get("upload_status") is None and not state.get("unknown_upload_confirmed_ended"):
        return {"outcome": "unknown_upload_retained", "project_id": project_id}
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


def execute(transport, state, persist, expected_digest, *, max_polls=120, sleep=time.sleep, fixture_module=None):
    """Never retry writes; journal identities before upload and retain unknown jobs."""
    details = plan(fixture_module)
    try:
        state["phase"] = "capabilities"
        capabilities = require(transport.request("GET", "/api/system/capabilities"), 200)
        if capabilities.get("max_upload_mb") != 1024 or capabilities.get("max_audio_seconds", 0) < details["duration_seconds"]:
            raise PreflightError("actual_app_caps_incompatible")
        # Coarse public capability value cannot replace measured filesystem budget.
        if capabilities.get("disk_free_gb", 0) * 1e9 < MIN_LOCAL_BYTES:
            raise PreflightError("reported_disk_below_policy")
        state["create_attempted"] = True
        state["phase"] = "create"
        persist()
        created = require(transport.request("POST", "/api/projects", {"name": state["project_name"]}), 201)
        if not isinstance(created.get("id"), str) or not created["id"]:
            raise PreflightError("missing_project_identity")
        state["project_id"] = created["id"]
        persist()
        state["upload_attempted"] = True
        state["phase"] = "upload"
        persist()
        upload = transport.upload(state["project_id"], expected_digest)
        state["upload_status"] = upload["status"]
        state.update({k: v for k, v in upload.items() if k.startswith("sent_")})
        result = require(upload, 202)
        state["source_id"], state["job_id"] = result["source"]["id"], result["job"]["id"]
        state["outcome"] = "accepted_not_completed"
        state["phase"] = "job_poll"
        persist()
        for _ in range(max_polls):
            job = require(transport.request("GET", "/api/jobs/" + quote(state["job_id"], safe="")), 200)
            state["job_status"] = job.get("status")
            if state["job_status"] in TERMINAL:
                source = require(transport.request("GET", "/api/sources/" + quote(state["source_id"], safe="")), 200)
                state["phase"] = "verify_import"
                state["server_original_sha256"] = source.get("content_hash")
                state["metadata"] = source.get("metadata")
                state["duration_seconds"] = source.get("duration_seconds")
                state["outcome"] = "verified_import" if (
                    state["job_status"] == "completed"
                    and source.get("content_hash") == expected_digest
                    and source.get("metadata", {}).get("sample_rate") == RATE
                    and source.get("metadata", {}).get("channels") == details["channels"]
                    and abs(source.get("duration_seconds", 0) - details["duration_seconds"]) < 0.01
                ) else "terminal_job_not_verified"
                break
            sleep(15)
        else:
            state["outcome"] = "poll_limit_unknown_retained"
    except (OSError, http.client.HTTPException, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # Do not serialize arbitrary SDK/network exception text containing credentials.
        state["outcome"] = "failed_or_unknown"
        state["error_class"] = type(exc).__name__
        if isinstance(exc, PreflightError):
            state["error_reason"], state["http_status"] = exc.reason, exc.status
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
    parser.add_argument("--fixture", choices=("four-channel","combined"),default="four-channel")
    parser.add_argument("--cleanup-receipt", type=Path, help="explicitly retry only verified owned-project metadata cleanup")
    parser.add_argument("--confirm-unknown-upload-ended", action="store_true",
                        help="operator independently verified no request/job is still being created")
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--origin")
    parser.add_argument("--final-source-sha")
    parser.add_argument("--password-file")
    parser.add_argument("--password-dpapi-file")
    parser.add_argument("--username", default="owner")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    fixture_module = None
    if args.fixture == "combined":
        import combined_fixture
        fixture_module = combined_fixture
    if not args.execute:
        print(json.dumps(plan(fixture_module), indent=2))
        return 0
    if not all((args.budget, args.origin, args.final_source_sha, args.out or args.cleanup_receipt)):
        parser.error("execution requires budget, final origin/source SHA and private receipt output")
    budget = validate_budget(json.loads(args.budget.read_text()), args.origin, args.final_source_sha,
                             cleanup_only=bool(args.cleanup_receipt),fixture_module=fixture_module)
    password = private_password(args)
    if not password:
        parser.error("private password source required")
    if args.cleanup_receipt:
        state = json.loads(args.cleanup_receipt.read_text())
        if state.get("origin") != args.origin or state.get("final_source_sha") != args.final_source_sha:
            parser.error("cleanup receipt origin/source mismatch")
        if not re.fullmatch(r"valid-gib-preflight-[0-9a-f]{32}", state.get("project_name", "")):
            parser.error("receipt is not an owned synthetic-test project")
        if args.confirm_unknown_upload_ended:
            state["unknown_upload_confirmed_ended"] = True
        transport = Transport(args.origin, args.username, password, budget)
        result = cleanup_owned(transport, state)
        state["cleanup"] = result
        text = json.dumps(state, indent=2)
        if password in text:
            raise RuntimeError("refusing credential-bearing receipt")
        args.cleanup_receipt.write_text(text, encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["outcome"] in ("metadata_removed", "already_absent") else 1
    if args.out.exists():
        parser.error("receipt already exists; refusing to repeat an unknown prior upload")
    digest = hashlib.sha256()
    for piece in (chunks() if fixture_module is None else fixture_module.chunks()):
        digest.update(piece)
    state = {"plan": plan(fixture_module), "origin": args.origin, "final_source_sha": args.final_source_sha,
             "expected_file_sha256": digest.hexdigest(),
             "project_name": "valid-gib-preflight-" + secrets.token_hex(16),
             "outcome": "not_started"}
    def persist():
        text = json.dumps(state, indent=2)
        if password in text:
            raise RuntimeError("refusing credential-bearing receipt")
        partial = args.out.with_name(args.out.name + "." + secrets.token_hex(16) + ".tmp")
        created = False
        try:
            with partial.open("x", encoding="utf-8") as receipt:
                created = True
                receipt.write(text)
                receipt.flush()
                os.fsync(receipt.fileno())
            os.replace(partial, args.out)
        finally:
            if created:
                partial.unlink(missing_ok=True)
    with args.out.open("x", encoding="utf-8") as receipt:
        receipt.write(json.dumps(state, indent=2))
    transport = Transport(args.origin, args.username, password, budget,fixture_module=fixture_module)
    execute(transport, state, persist, digest.hexdigest(),fixture_module=fixture_module)
    print(json.dumps({"outcome": state["outcome"], "cleanup": state.get("cleanup")}, indent=2))
    return 0 if state["outcome"] == "verified_import" and state.get("cleanup", {}).get("outcome") in (
        "metadata_removed", "already_absent"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
