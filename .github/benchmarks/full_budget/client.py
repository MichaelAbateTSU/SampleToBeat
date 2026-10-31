"""One bounded external-file journey; same descriptor, no automatic retry."""
import json
from pathlib import Path
import sys
import time

from budget_probe import Ledger, MeasuredBody
import verify_external_durable_http as E


def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    exact = sys.argv[3] == "combined"
    E.H = E.load(root / "app/scripts/verify_durable_http.py", "budget_existing")
    H, L = E.H, E.L
    L.H = H
    original = E.fixture_manifest(root / "fixture/fixture-manifest.json")
    seconds = E.validate_guards(original, 1 << 30, 512, 1800, exact=exact)
    ledger = Ledger(root / "evidence/client-events.jsonl")

    class Response(MeasuredBody):
        def __enter__(self):
            self.body.__enter__()
            return self

        def __exit__(self, *args):
            return self.body.__exit__(*args)

    class Client(L.client_type(H)):
        def json(self, method, path, *args, **kwargs):
            result = super().json(method, path, *args, **kwargs)
            if method == "GET" and path.startswith("/api/jobs/") and isinstance(result, dict):
                status = result.get("status")
                stage = result.get("stage")
                safe_stage = stage if isinstance(stage, str) and H.re.fullmatch(r"[a-zA-Z_-]{1,48}", stage) else "other_or_unavailable"
                ledger.record("job_observation", status=status if status in
                              ("queued", "running", "completed", "failed", "canceled", "interrupted") else "unknown",
                              stage=safe_stage)
            return result

        def wait(self, job):
            start = time.monotonic()
            kind = job.get("job_type")
            safe_kind = kind if kind in ("import", "generate", "export") else "other"
            ledger.record("job_wait", kind=safe_kind, outcome="started")
            try:
                result = super().wait(job)
                ledger.record("job_wait", kind=safe_kind, outcome="completed",
                              seconds=time.monotonic() - start)
                return result
            except Exception:
                ledger.record("job_wait", kind=safe_kind, outcome="failed_or_timeout",
                              seconds=time.monotonic() - start)
                raise

        def open(self, method, path, **kwargs):
            identity = {"request": __import__("os").urandom(8).hex(), "method": method}
            start = time.monotonic()
            raw = kwargs.get("raw")
            declared = int((kwargs.get("headers") or {}).get("Content-Length", 0))
            if raw is not None and not isinstance(raw, bytes):
                def measured():
                    for piece in raw:
                        ledger.record("http_upload_yield", **identity, bytes=len(piece))
                        yield piece
                kwargs["raw"] = measured()
            ledger.record("http_request", **identity,
                          request_kind="job" if path.startswith("/api/jobs/") else
                          "audio" if "/audio" in path or "/download" in path or path.endswith(".wav") else
                          "health" if path == "/api/health" else "metadata_or_mutation",
                          declared_upload_bytes=declared,
                          buffered_upload_bytes=len(raw) if isinstance(raw, bytes) else 0)
            try:
                response = super().open(method, path, **kwargs)
                ledger.record("http_status", **identity, status=response.status,
                              elapsed_seconds=time.monotonic() - start)
                return Response(response, ledger, "http_download", **identity)
            except Exception:
                ledger.record("http_failed", **identity, elapsed_seconds=time.monotonic() - start,
                              commit_state="unknown_if_mutating_no_automatic_retry")
                raise

    base = "http://127.0.0.1:8765"
    client = Client(base, base, root / "config/auth.json", allow_loopback_http=True,
                    timeout=120, job_timeout=1800)
    client.configure_long(seconds, 512, 1800)
    client.external_input = original
    manifest = root / "acceptance/manifest.json"
    if phase == "prepare":
        stream, before = E.verified_stream(root / "fixture/input.wav", original)
        try:
            L.prepare(client, manifest, None, external={
                "sha256": original["sha256"],
                "upload": lambda c, target: E.upload(c, target, stream, before, original)})
        finally:
            stream.close()
    elif phase == "verify":
        L.verify(client, manifest)
    else:
        raise ValueError("Unknown phase")


if __name__ == "__main__":
    try:
        main()
        print("External synthetic phase passed; no automatic retry or provider success inferred.")
    except Exception as error:
        safe = str(error) if E.H and isinstance(error, E.H.AcceptanceError) else "Local/response failure; details suppressed."
        print("External synthetic phase failed: " + safe)
        raise SystemExit(1)
