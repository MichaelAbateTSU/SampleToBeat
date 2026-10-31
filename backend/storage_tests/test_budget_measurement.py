"""Repeatable opt-in isolated budget journey using the supplied image's app."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from test_durable import remote, worker_env
from choplab import store
from choplab.object_storage import S3ByteStorage
from budget_probe import Ledger, MeasuredBody, cgroup, summarize


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(remote):
    """Catalog and physical objects are separate measurements; keys stay private."""
    from choplab import durable
    root, settings, _ = remote
    adapter = S3ByteStorage(settings, root.parent)
    objects = []
    for page in adapter.client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.bucket, Prefix=settings.prefix + "/publications/"):
        objects.extend(page.get("Contents", []))
    with store._conn() as con:
        rows = con.execute("SELECT state,ref FROM choplab_assets").fetchall()
    states = {}
    for state, raw in rows:
        item = states.setdefault(state, {"objects": 0, "declared_object_bytes": 0})
        item["objects"] += 1
        item["declared_object_bytes"] += json.loads(raw)["size"] if raw else 0
    # Classify document-backed artifact sizes independently of shared refs.
    classes = {}
    with store._conn() as con:
        documents = con.execute("SELECT kind,doc FROM docs").fetchall()
        asset_rows = con.execute("SELECT id,relative,ref FROM choplab_assets WHERE state='live'").fetchall()
    asset_map = {id: (relative, json.loads(raw)["size"]) for id, relative, raw in asset_rows}
    seen = set()
    for kind, raw in documents:
        doc = json.loads(raw)
        for id in doc.get("_assets", {}).values():
            if id in seen:
                continue
            seen.add(id)
            relative, size = asset_map[id]
            if kind == "source":
                category = "other_source"
                for field, label in (("original_file_path", "original"), ("internal_audio_path", "decoded"),
                                     ("features_path", "features")):
                    if doc.get(field) == "asset-path:" + relative:
                        category = label
                        break
                if "asset-path:" + relative in (doc.get("stems") or {}).values():
                    category = "stems"
            else:
                category = kind
            item = classes.setdefault(category, {"objects": 0, "bytes": 0})
            item["objects"] += 1
            item["bytes"] += size
    return {"catalog_states": states, "artifact_classes": classes,
            "provider_conservative_recorded_bytes_without_reclamation": sum(item["declared_object_bytes"] for item in states.values()),
            "catalog_objects_without_recorded_size": sum(not raw for _, raw in rows),
            "physical_listed_objects": len(objects),
            "physical_listed_object_bytes": sum(item["Size"] for item in objects),
            "limits": "Observer LIST/SQL traffic excluded from application budget. MinIO bucket is isolated; no Neon soft-retention assumption."}


def test_synthetic_budget(remote):
    if os.environ.get("CHOPLAB_SYNTHETIC_BUDGET") != "yes":
        pytest.skip("Requires explicit isolated synthetic budget opt-in")
    root, settings, _ = remote
    # The fixture enforces postgres/s3 synthetic hosts and known throwaway creds.
    # Keep each test's own prefix so previous synthetic CI runs do not inflate
    # physical inventory. No deletion of an entire shared bucket is needed.
    from dataclasses import replace, asdict
    from choplab import durable
    seconds = int(os.environ.get("CHOPLAB_BUDGET_SECONDS", "0"))
    assert seconds == 0 or 12 <= seconds <= 1200
    settings = replace(settings, prefix="budget-" + os.urandom(8).hex(),
                       max_bytes=512 << 20 if seconds else settings.max_bytes)
    config = remote[2]
    config.write_text(json.dumps(asdict(settings)))
    durable._settings = settings
    remote = (root, settings, config)
    harness_path = Path(os.environ.get("CHOPLAB_BUDGET_HARNESS", Path(__file__).resolve().parents[2] / "scripts" / "verify_durable_http.py"))
    H = load(harness_path, "budget_harness")
    long = None
    Base = H.Client
    if seconds:
        companion = os.environ.get("CHOPLAB_BUDGET_LONG_COMPANION")
        assert companion, "Long test requires supplied session companion and its sibling fixture"
        long = load(Path(companion), "budget_long_harness")
        long.H = H
        Base = long.client_type(H)
        long.guards(seconds, 512, 1800)
    directory = root.parent
    auth = directory / "private-http-auth.json"
    auth.write_text(json.dumps({"username": "owner", "password": "synthetic-http-only"}))
    auth.chmod(0o600)
    salt = b"synthetic-budget-only-salt"
    hashed = hashlib.pbkdf2_hmac("sha256", b"synthetic-http-only", salt, 600000)
    secret = directory / "private-http-hash"
    secret.write_text(f"pbkdf2_sha256$600000${salt.hex()}${hashed.hex()}")
    secret.chmod(0o600)
    manifest = directory / "budget-manifest.json"
    server_ledger, http_ledger = directory / "object-events.jsonl", directory / "http-events.jsonl"
    http = Ledger(http_ledger)

    class Response(MeasuredBody):
        def __enter__(self):
            self.body.__enter__()
            return self
        def __exit__(self, *args):
            return self.body.__exit__(*args)

    class Client(Base):
        def open(self, method, path, **kwargs):
            # Paths and auth/request headers are deliberately not persisted.
            identity = {"request": os.urandom(8).hex(), "method": method}
            http.record("http_request", **identity, raw_upload_bytes=len(kwargs.get("raw") or b""))
            try:
                response = super().open(method, path, **kwargs)
                http.record("http_status", **identity, status=response.status)
                return Response(response, http, "http_download", **identity)
            except Exception:
                http.record("http_failed", **identity)
                raise

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    client = Client(base, base, auth, allow_loopback_http=True, timeout=120,
                    job_timeout=1800 if seconds else 300)
    if seconds:
        client.configure_long(seconds, 512, 1800)
    env = {**worker_env(remote, root), "CHOPLAB_MODE": "hosted",
           "CHOPLAB_PUBLIC_URL": "https://localhost", "CHOPLAB_PASSWORD_HASH_FILE": str(secret)}
    log = directory / "private-synthetic-server.log"
    probe = Path(__file__).with_name("budget_probe.py")
    health = []
    stop = threading.Event()

    def health_probe():
        from urllib.request import urlopen
        while not stop.is_set():
            try:
                with urlopen(base + "/api/health", timeout=1) as response:
                    health.append(response.status)
            except Exception:
                health.append(0)
            stop.wait(.25)
    observer = threading.Thread(target=health_probe, daemon=True)
    observer.start()
    process = None
    report = {"format": "choplab-synthetic-budget-1", "seconds": seconds or "short-default",
              "revision": os.environ.get("CHOPLAB_BUDGET_REVISION", "unspecified"),
              "phases": [], "success": False}

    def events(path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def snapshot(phase):
        report["phases"].append({"name": phase, "object_inventory": inventory(remote),
                                 "application_object_traffic": summarize(events(server_ledger)),
                                 "http_body_download_bytes": sum(event["bytes"] for event in events(http_ledger)
                                                                 if event["category"] == "http_download"),
                                 "http_request_upload_bytes": sum(event["raw_upload_bytes"] for event in events(http_ledger)
                                                                  if event["category"] == "http_request")})

    def start(cache):
        temp = directory / ("temp-" + cache.name)
        temp.mkdir()
        with log.open("ab") as output:
            child = subprocess.Popen(
                [sys.executable, str(probe), str(server_ledger), str(listener.fileno()), str(cache), str(temp)],
                env={**env, "CHOPLAB_DATA": str(cache), "TMPDIR": str(temp)},
                pass_fds=(listener.fileno(),), stdout=output, stderr=output)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Synthetic application stopped; private test log preserved")
            if health and health[-1] == 200:
                try:
                    H.preflight(client)
                    return child
                except H.AcceptanceError:
                    pass
            time.sleep(.1)
        child.kill()
        child.wait(timeout=10)
        raise RuntimeError("Synthetic ownership/readiness deadline exceeded")

    try:
        process = start(root)
        if seconds:
            fixture_module = load(Path(os.environ["CHOPLAB_BUDGET_LONG_COMPANION"]).with_name("verify_import_interruption.py"), "budget_long_fixture")
            fixture_module.H = H
            saved = long.prepare(client, manifest, fixture_module.fixture)
        else:
            saved = H.prepare(client, manifest)
        snapshot("prepared")
        process.kill()
        process.wait(timeout=10)
        process = None
        shutil.rmtree(root)  # Exact fixture-owned cache; no broad/shared deletion.
        with store._conn() as con:
            con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
        cache = directory / "budget-replacement"
        process = start(cache)
        if seconds:
            long.verify(client, manifest)  # This test performed the replacement itself.
        else:
            H.verify(client, manifest)
        snapshot("verified-after-destroyed-cache")
        if os.environ.get("CHOPLAB_BUDGET_ORIGINAL_READ") == "yes":
            original_size = 44 + seconds * 16000 * 4 if seconds else len(H.synthetic_audio())
            code = """
import os,sys
sys.path.insert(0,os.environ['BUDGET_PROBE_DIR'])
from budget_probe import verify_original
verify_original(os.environ['BUDGET_LEDGER'],os.environ['BUDGET_SOURCE'],
                os.environ['BUDGET_SHA'],int(os.environ['BUDGET_SIZE']))
"""
            result = subprocess.run(
                [sys.executable, "-c", code],
                env={**env, "CHOPLAB_DATA": str(cache),
                     "BUDGET_PROBE_DIR": str(probe.parent), "BUDGET_LEDGER": str(server_ledger),
                     "BUDGET_SOURCE": saved["ids"]["source"], "BUDGET_SHA": saved["sha256"]["original_upload"],
                     "BUDGET_SIZE": str(original_size)}, capture_output=True, timeout=1800 if seconds else 60)
            assert result.returncode == 0, "Exact synthetic original-object verification failed; details suppressed"
            report["original_object_verified"] = {"sha256": saved["sha256"]["original_upload"], "bytes": original_size}
            snapshot("optional-original-read-after-replacement")
        process.terminate()
        process.wait(timeout=30)
        process = None
        # Exact synthetic deletion exercise, after the app stopped. It does not
        # alter the completed acceptance manifest or any real library.
        code = """
from choplab import durable,jobs,store
durable.configure()
with jobs.runtime():
 store.delete('variation', __import__('os').environ['SYNTHETIC_VARIATION'])
"""
        deleted = subprocess.run([sys.executable, "-c", code],
                                 env={**env, "CHOPLAB_DATA": str(cache), "SYNTHETIC_VARIATION": saved["ids"]["variation"]},
                                 capture_output=True, timeout=30)
        assert deleted.returncode == 0, "Synthetic retirement failed; output suppressed"
        snapshot("retired-before-reconciliation")
        code = """
from choplab import durable,jobs
from pathlib import Path
import os,sys
sys.path.insert(0,os.environ['BUDGET_PROBE_DIR'])
from budget_probe import Ledger,instrument_s3
instrument_s3(Ledger(os.environ['BUDGET_LEDGER']))
durable.configure()
with jobs.runtime(): durable.reconcile()
"""
        reconciled = subprocess.run([sys.executable, "-c", code],
                                    env={**env, "CHOPLAB_DATA": str(cache),
                                         "BUDGET_LEDGER": str(server_ledger), "BUDGET_PROBE_DIR": str(probe.parent)},
                                    capture_output=True, timeout=60)
        assert reconciled.returncode == 0, "Synthetic reconciliation failed; output suppressed"
        snapshot("reconciled-physical-minio-deletion")
        # A guaranteed absent key in this test's unique namespace measures a
        # real failed GET, not a production fault or an unsafe automatic retry.
        code = """
import os,sys
sys.path.insert(0,os.environ['BUDGET_PROBE_DIR'])
from budget_probe import Ledger,instrument_s3
from choplab import durable
from choplab.object_storage import S3ByteStorage
from choplab.core import DATA_DIR
instrument_s3(Ledger(os.environ['BUDGET_LEDGER']))
durable.configure()
adapter=S3ByteStorage(durable._settings,DATA_DIR)
try:
 adapter.client.get_object(Bucket=adapter.settings.bucket,Key=adapter.settings.prefix+'/never-published')
except adapter._client_error as error:
 assert error.response['Error']['Code']=='NoSuchKey'
else: raise RuntimeError('Synthetic missing-object expectation failed')
"""
        failed = subprocess.run([sys.executable, "-c", code],
                                env={**env, "CHOPLAB_DATA": str(cache), "BUDGET_LEDGER": str(server_ledger),
                                     "BUDGET_PROBE_DIR": str(probe.parent)}, capture_output=True, timeout=30)
        assert failed.returncode == 0, "Synthetic failed GET accounting probe failed"
        report["success"] = True
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        stop.set()
        observer.join(timeout=3)
        listener.close()
        report["application_object_traffic"] = summarize(events(server_ledger))
        http_events = events(http_ledger)
        report["application_http"] = {
            "requests": sum(event["category"] == "http_request" for event in http_events),
            "failed_requests": sum(event["category"] == "http_failed" for event in http_events),
            "response_body_bytes_consumed": sum(event["bytes"] for event in http_events if event["category"] == "http_download"),
            "multipart_input_body_bytes": sum(event["raw_upload_bytes"] for event in http_events if event["category"] == "http_request")}
        report["health"] = {"samples": len(health), "healthy": health.count(200),
                            "unavailable_including_intentional_restart": health.count(0)}
        report["cgroup"] = cgroup()
        operations = report["application_object_traffic"]["operations"]
        uploads = sum(item["upload_body_bytes_read_or_buffered"] for operation, item in operations.items()
                      if operation in ("PutObject", "UploadPart"))
        downloads = sum(item["object_body_bytes_consumed"] for item in operations.values())
        report["original_object_verification_optional_requested"] = os.environ.get("CHOPLAB_BUDGET_ORIGINAL_READ") == "yes"
        report["object_call_outcomes"] = [
            {key: event[key] for key in ("call", "operation", "object_sha256", "outcome", "code", "declared_object_bytes")
             if key in event}
            for event in events(server_ledger) if event["category"] == "s3_call" and event["outcome"] != "started"]
        report["accounting"] = {
            "render_to_neon_object_upload_payload_observed": uploads,
            "neon_to_render_object_download_payload_consumed": downloads,
            "render_to_acceptance_client_response_payload_consumed": report["application_http"]["response_body_bytes_consumed"],
            "render_outbound_observed_payload_sum_not_wire_floor": uploads + report["application_http"]["response_body_bytes_consumed"],
            "neon_public_object_payload_directional_sum_not_billing_claim": uploads + downloads,
            "unobserved": ["PostgreSQL/TLS/network overhead/retransmissions and actual peer acknowledgment",
                           "health probe and observer LIST/SQL bytes; object error-response body bytes",
                           "Neon shared branch/project traffic from other consumers and soft-deleted retention",
                           "Render platform probes/static frontend serving/provider quota metering",
                           "MinIO/DB disk and memory; container writable files outside app cache/temp"],
            "note": "Only app body consumption/buffering is observable here. Inbound fixture upload is not Render outbound. Object GET payload is Neon-to-Render, distinct from Render-to-client response. Sampled peaks are lower bounds, not exact maxima."}
        report["accounting"]["note"] += " Optional direct original-object GET is counted in Neon-to-Render object reads, not as an application HTTP download."
        if report["success"]:
            try:
                prepared = report["phases"][0]["object_inventory"]
                assert uploads >= prepared["physical_listed_object_bytes"]
                if report["application_object_traffic"]["retry_attempts"] == 0:
                    assert uploads == prepared["physical_listed_object_bytes"]
                assert downloads > 0 and report["application_http"]["response_body_bytes_consumed"] > downloads
                assert operations["GetObject"]["failed_calls"] == 1
                assert report["application_object_traffic"]["disk_samples"] > 0
                retired = next(phase["object_inventory"] for phase in report["phases"] if phase["name"] == "retired-before-reconciliation")
                final = report["phases"][-1]["object_inventory"]
                assert retired["catalog_states"]["retired"]["objects"] == 1
                assert final["physical_listed_objects"] == prepared["physical_listed_objects"] - 1
                assert final["provider_conservative_recorded_bytes_without_reclamation"] == prepared["physical_listed_object_bytes"]
                if report["original_object_verification_optional_requested"]:
                    assert report["original_object_verified"]["bytes"] == prepared["artifact_classes"]["original"]["bytes"]
            except (AssertionError, KeyError, StopIteration):
                report["success"] = False
                report["failure"] = "Measured payload/inventory/original verification accounting did not agree."
        output = Path(os.environ.get("CHOPLAB_BUDGET_REPORT", directory / "budget-report.json"))
        output.write_text(json.dumps(report, indent=2))
        auth.unlink(missing_ok=True)
        secret.unlink(missing_ok=True)
        print("SYNTHETIC_BUDGET " + json.dumps({"success": report["success"], "accounting": report["accounting"],
                                               "disk_peak_sampled": report["application_object_traffic"]["disk_sampled_peak_allocated_bytes"]}))
    assert report["success"]
