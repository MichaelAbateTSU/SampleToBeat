import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import client
import fixture


def budget():
    now = datetime.now(timezone.utc)
    return {"approved": True, "origin": "https://example.invalid",
            "final_source_sha": "a" * 40,
            "measured_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "input_ingress_bytes": client.BODY_BYTES,
            "available_local_bytes": client.MIN_LOCAL_BYTES,
            "tmp_available_bytes": 5 << 30, "data_available_bytes": 5 << 30,
            "object_storage_available_bytes": client.MIN_OBJECT_BYTES,
            "app_egress_available_bytes": client.MIN_OBJECT_BYTES,
            "max_http_requests": 260, "max_upload_mb": 1024, "max_audio_seconds": 1200,
            "measurement_evidence": "offline test only",
            "analysis_extra_budget_acknowledged": True}


class FixtureTests(unittest.TestCase):
    def test_exact_header_independent_riff_lengths(self):
        h = fixture.header(fixture.FRAMES)
        self.assertEqual(len(h), 64)
        self.assertEqual(struct.unpack_from("<I", h, 4)[0] + 8, 1073741824)
        self.assertEqual(struct.unpack_from("<I", h, 60)[0], 1073741760)
        self.assertEqual(fixture.FRAMES * 12 + 64, fixture.FILE_BYTES)
        self.assertLessEqual(fixture.FRAMES / fixture.RATE, 1200)
        self.assertEqual(struct.unpack_from("<HHIIHH", h, 20),
                         (1, 4, 96000, 1152000, 12, 24))

    def test_small_reference_independent_samples_and_wave(self):
        frames = 1001
        generated = b"".join(fixture.chunks(frames))
        # Independent reference: per-frame formula, not generator block repetition.
        expected = bytearray()
        for f in range(frames):
            for channel in range(4):
                phase = (f + channel * 240) % 960
                ramp = min(phase, 960 - phase)
                value = (-1048576 + (2097152 * ramp // 480)) & 0xFFFFFF
                expected.extend(struct.pack("<I", value)[:3])
        self.assertEqual(generated[64:], expected)
        with wave.open(io.BytesIO(generated), "rb") as wav:
            self.assertEqual(wav.getnframes(), frames)
            self.assertEqual(wav.getnchannels(), 4)
            self.assertEqual(wav.getsampwidth(), 3)
            self.assertEqual(wav.getframerate(), 96000)
            self.assertEqual(wav.readframes(frames), expected)
        self.assertGreater(len(set(expected)), 10)
        self.assertNotEqual(expected, b"\x00" * len(expected))

    def test_partial_period_and_block_bounds(self):
        for n in (1, 959, 960, 961, fixture.BLOCK_FRAMES + 1):
            pieces = list(fixture.chunks(n))
            self.assertEqual(sum(map(len, pieces)), 64 + n * 12)
            self.assertLessEqual(max(map(len, pieces)), fixture.BLOCK_FRAMES * 12)

    def test_invalid_size_preflight(self):
        for n in (0, -1, fixture.FRAMES + 1):
            with self.assertRaises(ValueError):
                fixture.header(n)

    def test_exclusive_output_and_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fixture.wav"
            result = fixture.write(path, 1001)
            self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                fixture.write(path, 1)
            self.assertFalse(path.with_name(path.name + ".part").exists())
            path.unlink()
            def broken(_):
                yield b"RIFF"
                raise OSError("synthetic write failure")
            with patch.object(fixture, "chunks", broken), self.assertRaises(OSError):
                fixture.write(path, 1)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name(path.name + ".part").exists())

    def test_preserve_preexisting_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fixture.wav"
            partial = path.with_name(path.name + ".part")
            partial.write_bytes(b"user work")
            with self.assertRaises(FileExistsError):
                fixture.write(path, 1)
            self.assertEqual(partial.read_bytes(), b"user work")

    def test_disk_preflight_and_wrong_generator_length_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fixture.wav"
            with patch.object(fixture.shutil, "disk_usage", return_value=type("Disk", (), {"free":0})()):
                with self.assertRaises(OSError):
                    fixture.write(path, 1001)
            self.assertFalse(path.exists())
            with patch.object(fixture, "chunks", lambda _: iter([b"RIFF"])):
                with self.assertRaises(ValueError):
                    fixture.write(path, 1001)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name(path.name + ".part").exists())

    def test_explicit_cli_optin_and_no_network_default(self):
        run = subprocess.run([sys.executable, "client.py"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["file_bytes"], 1073741824)
        run = subprocess.run([sys.executable, "fixture.py", "--hash"], capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        run = subprocess.run([sys.executable, "fixture.py"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["file_bytes"], 1073741824)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "must-not-exist.wav"
            run = subprocess.run([sys.executable, "fixture.py", "--describe", "--write", str(path)],
                                 capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(path.exists())

    def test_budget_approval_freshness_and_caps(self):
        b = budget()
        client.validate_budget(b, b["origin"], b["final_source_sha"])
        for key in ("approved", "input_ingress_bytes", "available_local_bytes",
                    "tmp_available_bytes", "data_available_bytes",
                    "object_storage_available_bytes", "app_egress_available_bytes",
                    "max_http_requests", "measurement_evidence", "analysis_extra_budget_acknowledged"):
            altered = dict(b)
            altered[key] = False if key == "approved" else 0
            with self.assertRaises(ValueError, msg=key):
                client.validate_budget(altered, b["origin"], b["final_source_sha"])
        altered = dict(b, measured_at=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat())
        with self.assertRaises(ValueError):
            client.validate_budget(altered, b["origin"], b["final_source_sha"])
        with self.assertRaises(ValueError):
            client.validate_budget(b, "https://wrong.invalid", b["final_source_sha"])
        with self.assertRaises(ValueError):
            client.validate_budget(b, b["origin"], "badsha")


class FakeTransport:
    calls = 0
    def __init__(self, *, unknown_upload=False, active=False, rejected=False, wrong_identity=False):
        self.unknown_upload = unknown_upload
        self.active, self.rejected, self.wrong_identity = active, rejected, wrong_identity
        self.writes, self.deleted = [], False

    def request(self, method, path, body=None):
        self.calls += 1
        if method in ("POST", "DELETE"):
            self.writes.append((method, path))
        if path == "/api/system/capabilities":
            return {"status":200,"body":{"max_upload_mb":1024,"max_audio_seconds":1200,"disk_free_gb":10}}
        if method == "POST":
            return {"status":201,"body":{"id":"prj_test"}}
        if path.endswith("/jobs"):
            return {"status":200,"body":[{"status":"running" if self.active else "failed"}]}
        if path.startswith("/api/jobs/"):
            return {"status":200,"body":{"status":"running" if self.active else "completed"}}
        if path.startswith("/api/sources/"):
            return {"status":200,"body":{"content_hash":"digest",
                    "metadata":{"sample_rate":96000,"channels":4},
                    "duration_seconds":fixture.FRAMES/96000}}
        if method == "DELETE":
            self.deleted = True
            return {"status":204,"body":{}}
        return {"status":404 if self.deleted else 200,
                "body":{"id":"prj_test","name":"foreign" if self.wrong_identity else "valid-gib-preflight-test"}}

    def upload(self, project_id, digest):
        self.calls += 1
        self.writes.append(("UPLOAD", project_id))
        if self.unknown_upload:
            raise TimeoutError("unknown upload")
        if self.rejected:
            return {"status":413,"body":{}}
        return {"status":202,"body":{"source":{"id":"src_test"},"job":{"id":"job_test"}}}


class ClientTests(unittest.TestCase):
    def state(self):
        return {"project_name":"valid-gib-preflight-test"}

    def test_unknown_upload_never_retried_active_job_retained(self):
        transport, state = FakeTransport(unknown_upload=True, active=True), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertEqual(sum(m == "UPLOAD" for m, _ in transport.writes), 1)
        self.assertFalse(transport.deleted)
        self.assertEqual(state["cleanup"]["outcome"], "unknown_upload_retained")
        self.assertEqual(state["outcome"], "failed_or_unknown")

    def test_unknown_upload_no_job_yet_is_not_deleted(self):
        transport, state = FakeTransport(unknown_upload=True, active=False), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertFalse(transport.deleted)
        self.assertEqual(state["cleanup"]["outcome"], "unknown_upload_retained")

    def test_cleanup_only_budget_does_not_require_spent_upload_quota(self):
        b = budget()
        b["input_ingress_bytes"] = b["object_storage_available_bytes"] = b["app_egress_available_bytes"] = 0
        b["max_http_requests"] = 10
        client.validate_budget(b, b["origin"], b["final_source_sha"], cleanup_only=True)
        with self.assertRaises(ValueError):
            client.validate_budget(b, b["origin"], b["final_source_sha"])

    def test_verified_import_metadata_cleanup_not_object_claim(self):
        transport, state = FakeTransport(), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertEqual(state["outcome"], "verified_import")
        self.assertTrue(transport.deleted)
        self.assertIn("not established", state["cleanup"]["object_cleanup"])

    def test_rejection_clean_only_owned_project(self):
        transport, state = FakeTransport(rejected=True), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertTrue(transport.deleted)
        self.assertEqual(state["upload_status"], 413)
        self.assertEqual(state["outcome"], "failed_or_unknown")

    def test_wrong_identity_never_deleted(self):
        transport, state = FakeTransport(wrong_identity=True), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertFalse(transport.deleted)
        self.assertEqual(state["cleanup"]["outcome"], "identity_mismatch_retained")

    def test_unknown_creation_never_guessed_or_deleted(self):
        state = self.state()
        self.assertEqual(client.cleanup_owned(FakeTransport(), state)["outcome"], "unknown_create_retained")

    def test_accepted_active_jobs_not_deleted_even_with_known_id(self):
        transport, state = FakeTransport(active=True), self.state()
        client.execute(transport, state, lambda:None, "digest", max_polls=1, sleep=lambda _:None)
        self.assertEqual(state["outcome"], "poll_limit_unknown_retained")
        self.assertEqual(state["cleanup"]["outcome"], "active_or_unknown_jobs_retained")
        self.assertFalse(transport.deleted)

    def test_unknown_upload_confirmation_keeps_active_job_guard(self):
        transport = FakeTransport(active=True)
        state = {"project_id":"prj_test","project_name":"valid-gib-preflight-test",
                 "upload_attempted":True,"unknown_upload_confirmed_ended":True}
        self.assertEqual(client.cleanup_owned(transport, state)["outcome"], "active_or_unknown_jobs_retained")
        self.assertFalse(transport.deleted)

    def test_cli_no_approval_causes_zero_connects(self):
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "budget.json"
            b = budget(); b["approved"] = False
            bp.write_text(json.dumps(b))
            with patch.object(sys, "argv", ["client.py", "--execute", "--budget", str(bp),
                 "--origin", b["origin"], "--final-source-sha", b["final_source_sha"],
                 "--out", str(Path(d) / "receipt.json")]), patch.object(client.Transport, "connect") as connect:
                with self.assertRaises(ValueError):
                    client.main()
                connect.assert_not_called()

    def test_http_only_offline_loopback(self):
        with self.assertRaises(ValueError):
            client.Transport("http://provider.invalid", "owner", "synthetic", budget(), allow_loopback=True)
        with self.assertRaises(ValueError):
            client.Transport("https://owner:secret@example.invalid", "owner", "synthetic", budget())

    def test_real_loopback_stream_framing_digest(self):
        frames = 1001
        wav = b"".join(fixture.chunks(frames))
        observed = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed["path"] = self.path
                observed["auth"] = self.headers.get("Authorization", "")
                n = int(self.headers["Content-Length"])
                h, body = hashlib.sha256(), bytearray()
                while n:
                    piece = self.rfile.read(min(n, 4096))
                    h.update(piece)
                    body.extend(piece)
                    n -= len(piece)
                observed["digest"], observed["body"] = h.hexdigest(), bytes(body)
                self.send_response(202)
                data = b'{"source":{"id":"test"},"job":{"id":"test"}}'
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            transport = client.Transport(f"http://127.0.0.1:{server.server_port}", "owner", "synthetic",
                                         budget(), allow_loopback=True)
            with patch.object(client, "chunks", lambda:fixture.chunks(frames)), \
                 patch.object(client, "BODY_BYTES", len(client.PREFIX)+len(wav)+len(client.SUFFIX)):
                response = transport.upload("prj_test", hashlib.sha256(wav).hexdigest())
            expected = client.PREFIX + wav + client.SUFFIX
            self.assertEqual(observed["body"], expected)
            self.assertEqual(observed["digest"], response["sent_multipart_sha256"])
            self.assertEqual(observed["path"], "/api/projects/prj_test/sources")
            self.assertTrue(observed["auth"].startswith("Basic "))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
