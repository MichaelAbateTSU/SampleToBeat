"""Disposable measurement probe; payload counts are not TLS/provider billing.

Never logs URLs, headers, object keys, metadata bodies or credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def record(self, category, **fields):
        with self.lock, self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"category": category, "monotonic": time.monotonic(), **fields},
                                 allow_nan=False) + "\n")


class MeasuredBody:
    def __init__(self, body, ledger, category, **identity):
        self.body, self.ledger, self.category, self.identity = body, ledger, category, identity

    def read(self, *args, **kwargs):
        try:
            data = self.body.read(*args, **kwargs)
        except Exception:
            self.ledger.record(self.category, **self.identity, bytes=0, outcome="read_failed")
            raise
        self.ledger.record(self.category, **self.identity, bytes=len(data or b""), outcome="read")
        return data

    def read1(self, *args, **kwargs):
        try:
            data = self.body.read1(*args, **kwargs)
        except Exception:
            self.ledger.record(self.category, **self.identity, bytes=0, outcome="read_failed")
            raise
        self.ledger.record(self.category, **self.identity, bytes=len(data or b""), outcome="read")
        return data

    def __getattr__(self, key):
        return getattr(self.body, key)


def instrument_s3(ledger):
    """Capture logical calls plus every botocore HTTP attempt, including retries."""
    from botocore.client import BaseClient
    from botocore.endpoint import Endpoint
    context = threading.local()
    call, send = BaseClient._make_api_call, Endpoint._send

    def make_call(self, operation, params):
        if self.meta.service_model.service_name != "s3":
            return call(self, operation, params)
        identity = {"call": os.urandom(8).hex(), "operation": operation,
                    "object_sha256": hashlib.sha256(str(params.get("Key", "")).encode()).hexdigest()}
        previous = getattr(context, "identity", None)
        context.identity = identity
        ledger.record("s3_call", **identity, outcome="started")
        try:
            result = call(self, operation, params)
            if operation == "GetObject" and "Body" in result:
                result["Body"] = MeasuredBody(result["Body"], ledger, "s3_download", **identity)
            ledger.record("s3_call", **identity, outcome="success",
                          declared_object_bytes=result.get("ContentLength", 0))
            return result
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            # Numeric status/known throttling codes only; never log arbitrary
            # server exception text or user-controlled error metadata.
            safe_code = code if code in ("SlowDown", "NoSuchKey", "AccessDenied") else "other"
            ledger.record("s3_call", **identity, outcome="failed", code=safe_code)
            raise
        finally:
            context.identity = previous

    def attempt(self, request):
        identity = getattr(context, "identity", None)
        if identity is None:
            return send(self, request)
        identity = {**identity, "attempt": os.urandom(8).hex()}
        length = request.headers.get("Content-Length", 0)
        try:
            length = int(length)
        except (ValueError, TypeError):
            length = None
        ledger.record("s3_attempt", **identity, outcome="started", declared_request_bytes=length)
        body = request.body
        if body is not None and hasattr(body, "read"):
            request.body = MeasuredBody(body, ledger, "s3_upload_read", **identity)
        elif isinstance(body, (bytes, bytearray)):
            ledger.record("s3_upload_buffer", **identity, bytes=len(body))
        try:
            response = send(self, request)
            ledger.record("s3_attempt", **identity, outcome="response", status=response.status_code)
            return response
        except Exception:
            ledger.record("s3_attempt", **identity, outcome="transport_failed")
            raise
        finally:
            request.body = body

    BaseClient._make_api_call, Endpoint._send = make_call, attempt


def disk_bytes(roots):
    """Allocated and logical bytes, de-duplicated by inode within each sample."""
    allocated, logical, seen, errors = 0, 0, set(), 0
    for root in roots:
        for parent, _, names in os.walk(root):
            for name in names:
                try:
                    info = os.stat(os.path.join(parent, name), follow_symlinks=False)
                    key = (info.st_dev, info.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    logical += info.st_size
                    allocated += getattr(info, "st_blocks", (info.st_size + 511) // 512) * 512
                except OSError:
                    errors += 1
    return {"logical_bytes": logical, "allocated_bytes": allocated, "sample_errors": errors}


def cgroup():
    root = Path("/sys/fs/cgroup")
    result = {}
    for name in ("memory.current", "memory.peak", "memory.max", "memory.swap.current", "memory.swap.max"):
        file = root / name
        if file.exists():
            value = file.read_text().strip()
            result[name] = int(value) if value.isdigit() else value
    events = root / "memory.events"
    if events.exists():
        result["memory.events"] = dict(line.split() for line in events.read_text().splitlines())
    return result


def serve(ledger_path, fd, cache, temp):
    if os.environ.get("CHOPLAB_DISPOSABLE_POSTGRES_TEST") != "yes":
        raise RuntimeError("Synthetic disposable opt-in required")
    ledger = Ledger(ledger_path)
    instrument_s3(ledger)
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            ledger.record("disk_sample", **disk_bytes([cache, temp]), cgroup=cgroup())
            stop.wait(.1)
    observer = threading.Thread(target=sample, daemon=True)
    observer.start()
    try:
        import uvicorn
        uvicorn.run("choplab.api:app", fd=fd, proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1", access_log=False)
    finally:
        stop.set()
        observer.join()
        ledger.record("disk_sample", **disk_bytes([cache, temp]), cgroup=cgroup())


def summarize(events):
    attempts = [event for event in events if event["category"] == "s3_attempt" and event["outcome"] == "started"]
    results = [event for event in events if event["category"] == "s3_attempt" and event["outcome"] != "started"]
    calls = [event for event in events if event["category"] == "s3_call" and event["outcome"] == "started"]
    operations = {}
    for operation in {event["operation"] for event in calls}:
        operations[operation] = {
            "logical_calls": sum(event["operation"] == operation for event in calls),
            "http_attempts": sum(event["operation"] == operation for event in attempts),
            "successful_calls": sum(event["category"] == "s3_call" and event["operation"] == operation
                                    and event["outcome"] == "success" for event in events),
            "failed_calls": sum(event["category"] == "s3_call" and event["operation"] == operation
                                and event["outcome"] == "failed" for event in events),
            "request_declared_bytes_attempted": sum(event.get("declared_request_bytes") or 0
                                                   for event in attempts if event["operation"] == operation),
            "upload_body_bytes_read_or_buffered": sum(event.get("bytes", 0) for event in events
                                                      if event["category"] in ("s3_upload_read", "s3_upload_buffer")
                                                      and event["operation"] == operation),
            "object_body_bytes_consumed": sum(event.get("bytes", 0) for event in events
                                              if event["category"] == "s3_download" and event["operation"] == operation),
        }
    samples = [event for event in events if event["category"] == "disk_sample"]
    per_attempt = []
    for event in attempts:
        id = event["attempt"]
        outcome = next((item for item in results if item.get("attempt") == id), None)
        per_attempt.append({
            "operation": event["operation"], "call": event["call"], "attempt": id,
            "object_sha256": event.get("object_sha256"),
            "declared_request_bytes": event.get("declared_request_bytes"),
            "body_bytes_read_or_buffered": sum(item.get("bytes", 0) for item in events
                                              if item.get("attempt") == id and item["category"] in ("s3_upload_read", "s3_upload_buffer")),
            "status": outcome.get("status") if outcome else None,
            "outcome": outcome["outcome"] if outcome else "unobserved_or_process_interrupted",
        })
    return {
        "operations": operations,
        "http_attempts": per_attempt,
        "retry_attempts": len(attempts) - len({event["call"] for event in attempts}),
        "http_failure_responses": sum(event.get("status", 0) >= 400 for event in results),
        "http_transport_failures": sum(event["outcome"] == "transport_failed" for event in results),
        "disk_sampled_peak_logical_bytes": max((event["logical_bytes"] for event in samples), default=0),
        "disk_sampled_peak_allocated_bytes": max((event["allocated_bytes"] for event in samples), default=0),
        "disk_samples": len(samples),
        "disk_sample_errors": sum(event["sample_errors"] for event in samples),
        "cgroup_last": samples[-1].get("cgroup") if samples else None,
        "limits": [
            "HTTP attempt declared bytes and body reads/buffers are not proof of bytes acknowledged by the remote peer.",
            "Failed GET error bodies, socket/TLS/header overhead, DB protocol traffic, SDK preflight reads and kernel retransmits are not counted.",
            "Disk peaks are sampled every 100ms; between-sample/open-unlinked files and files outside cache/temp are not observed.",
            "MinIO deletion is physical test behavior, not Neon soft-retention/quota-reclamation evidence.",
        ],
    }


def verify_original(ledger_path, source_id, expected_sha, expected_size):
    """Read only the exact document-linked live original, in isolated tests."""
    if os.environ.get("CHOPLAB_DISPOSABLE_POSTGRES_TEST") != "yes":
        raise RuntimeError("Synthetic disposable opt-in required")
    from choplab import durable, store
    from choplab.object_storage import ObjectRef
    ledger = Ledger(ledger_path)
    instrument_s3(ledger)
    durable.configure()
    source = store.get("source", source_id)
    relative = durable._relative(source["original_file_path"])
    id = source["_assets"][relative]
    with store._conn() as con:
        actual_relative, namespace, state, raw = durable._asset(con, id)
        assert con.execute("SELECT 1 FROM choplab_asset_links WHERE doc_id=? AND asset_id=?",
                           (source_id, id)).fetchone(), "Original must remain linked to the exact source"
    ref = ObjectRef(**json.loads(raw))
    assert state == "live" and actual_relative == relative
    assert ref.size == expected_size and ref.sha256 == expected_sha
    # Explicit remote read even if the working cache happens to have a copy.
    path = durable._adapter(namespace).materialize(ref, relative)
    assert path.stat().st_size == expected_size and durable._hash(path) == expected_sha
    ledger.record("original_verified", bytes=expected_size, sha256=expected_sha)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("fd", type=int)
    parser.add_argument("cache", type=Path)
    parser.add_argument("temp", type=Path)
    args = parser.parse_args()
    serve(args.ledger, args.fd, args.cache, args.temp)
