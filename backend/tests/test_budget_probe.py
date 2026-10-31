"""Offline accounting integrity tests with wholly synthetic transport events."""
import importlib.util
import io
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "budget_probe", Path(__file__).resolve().parents[1] / "storage_tests" / "budget_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_exact_body_consumption_and_read_failure_preserved(tmp_path):
    ledger = probe.Ledger(tmp_path / "events.jsonl")
    body = probe.MeasuredBody(io.BytesIO(b"synthetic"), ledger, "s3_download", operation="GetObject", call="a")
    assert body.read(3) == b"syn"
    assert body.read1() == b"thetic"
    assert body.read() == b""
    events = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert sum(event["bytes"] for event in events) == 9
    assert "synthetic" not in ledger.path.read_text()


def test_summary_counts_retries_failed_operations_and_disk_separately():
    events = [
        {"category": "s3_call", "operation": "UploadPart", "call": "a", "outcome": "started"},
        {"category": "s3_attempt", "operation": "UploadPart", "call": "a", "attempt": "1",
         "outcome": "started", "declared_request_bytes": 10},
        {"category": "s3_upload_read", "operation": "UploadPart", "bytes": 4},
        {"category": "s3_attempt", "operation": "UploadPart", "call": "a", "attempt": "1",
         "outcome": "response", "status": 503},
        {"category": "s3_attempt", "operation": "UploadPart", "call": "a", "attempt": "2",
         "outcome": "started", "declared_request_bytes": 10},
        {"category": "s3_upload_read", "operation": "UploadPart", "bytes": 10},
        {"category": "s3_attempt", "operation": "UploadPart", "outcome": "response", "status": 200},
        {"category": "s3_call", "operation": "UploadPart", "outcome": "success"},
        {"category": "s3_call", "operation": "DeleteObject", "call": "b", "outcome": "started"},
        {"category": "s3_attempt", "operation": "DeleteObject", "call": "b",
         "outcome": "started", "declared_request_bytes": 0},
        {"category": "s3_attempt", "operation": "DeleteObject", "outcome": "transport_failed"},
        {"category": "s3_call", "operation": "DeleteObject", "outcome": "failed"},
        {"category": "disk_sample", "logical_bytes": 100, "allocated_bytes": 4096, "sample_errors": 0},
        {"category": "disk_sample", "logical_bytes": 50, "allocated_bytes": 8192, "sample_errors": 1},
    ]
    result = probe.summarize(events)
    assert result["retry_attempts"] == 1
    assert result["http_failure_responses"] == 1 and result["http_transport_failures"] == 1
    assert result["operations"]["UploadPart"]["request_declared_bytes_attempted"] == 20
    assert result["operations"]["UploadPart"]["upload_body_bytes_read_or_buffered"] == 14
    assert result["operations"]["DeleteObject"]["failed_calls"] == 1
    assert result["disk_sampled_peak_allocated_bytes"] == 8192
    assert result["disk_sample_errors"] == 1


def test_disk_sampling_deduplicates_hardlinked_synthetic_files(tmp_path):
    path = tmp_path / "a"
    path.write_bytes(b"x" * 123)
    import os
    os.link(path, tmp_path / "b")
    measured = probe.disk_bytes([tmp_path, tmp_path])
    assert measured["logical_bytes"] == 123
    assert measured["sample_errors"] == 0


def test_botocore_calls_and_attempts_keep_private_fields_out_of_ledger(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from botocore.client import BaseClient
    from botocore.endpoint import Endpoint
    ledger = probe.Ledger(tmp_path / "ledger")
    attempts = []
    request = SimpleNamespace(headers={"Content-Length": "9"}, body=io.BytesIO(b"synthetic"))
    endpoint = object.__new__(Endpoint)
    def transport(self, req):
        assert req.body.read() == b"synthetic"
        attempts.append(1)
        return SimpleNamespace(status_code=503 if len(attempts) == 1 else 200)
    def api_call(self, operation, params):
        endpoint._send(request)
        request.body.seek(0)
        endpoint._send(request)
        return {"ContentLength": 9, "Body": io.BytesIO(b"synthetic")}
    monkeypatch.setattr(BaseClient, "_make_api_call", api_call)
    monkeypatch.setattr(Endpoint, "_send", transport)
    # Restore monkey-patched globals after instrumentation as well.
    probe.instrument_s3(ledger)
    client = SimpleNamespace(meta=SimpleNamespace(service_model=SimpleNamespace(service_name="s3")))
    result = BaseClient._make_api_call(client, "GetObject", {"Key": "private-key", "Secret": "never-log"})
    assert result["Body"].read() == b"synthetic"
    text = ledger.path.read_text()
    assert "private-key" not in text and "never-log" not in text and "synthetic" not in text
    summary = probe.summarize([json.loads(line) for line in text.splitlines()])
    assert summary["retry_attempts"] == 1
    assert summary["operations"]["GetObject"]["object_body_bytes_consumed"] == 9
    assert summary["operations"]["GetObject"]["upload_body_bytes_read_or_buffered"] == 18
