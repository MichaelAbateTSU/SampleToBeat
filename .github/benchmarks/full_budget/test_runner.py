import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

import verify_external_durable_http as E
from budget_probe import Ledger, MeasuredBody, summarize
from run import CommandFailure
import admin
import io


class FixtureGuards(unittest.TestCase):
    def setUp(self):
        class Harness:
            import re
            SHA = re.compile(r"[0-9a-f]{64}\Z")
            AcceptanceError = RuntimeError
        E.H = Harness
        E.L.H = Harness
        self.data = json.loads(Path(__file__).with_name("combined-wav-manifest.json").read_text())

    def test_combined_exact_and_short_control_are_separate(self):
        self.assertEqual(E.validate_guards(self.data, 1 << 30, 512, 1800), 1200)
        short = {**self.data, "size": 52 + 12 * 96000 * 8, "frames": 12 * 96000}
        self.assertEqual(E.validate_guards(short, 1 << 30, 512, 1800, exact=False), 12)
        with self.assertRaises(RuntimeError):
            E.validate_guards(short, 1 << 30, 512, 1800)

    def test_bad_duration_bytes_and_unverified_manifest_are_errors(self):
        for change in ({"size": (1 << 30) - 1}, {"frames": self.data["frames"] - 1}):
            with self.assertRaises(RuntimeError):
                E.validate_guards({**self.data, **change}, 1 << 30, 512, 1800)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "manifest.json"
            file.write_text(json.dumps({**self.data, "verified": False}))
            with self.assertRaises(RuntimeError):
                E.fixture_manifest(file)

    def test_unknown_commit_attempt_and_read_counts_are_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / "events")
            ledger.record("s3_call", operation="PutObject", call="1", outcome="started")
            ledger.record("s3_attempt", operation="PutObject", call="1", attempt="2",
                          outcome="started", declared_request_bytes=123)
            body = MeasuredBody(io.BytesIO(b"payload"), ledger, "s3_upload_read",
                                operation="PutObject", call="1", attempt="2")
            self.assertEqual(body.read(3), b"pay")
            raw = [json.loads(line) for line in ledger.path.read_text().splitlines()]
            report = summarize(raw)
            self.assertEqual(report["http_attempts"][0]["body_bytes_read_or_buffered"], 3)
            self.assertEqual(report["http_attempts"][0]["outcome"], "unobserved_or_process_interrupted")
            self.assertEqual(report["operations"]["PutObject"]["successful_calls"], 0)

    def test_subprocess_evidence_redacts_stderr_paths_and_values(self):
        result = subprocess.CompletedProcess(["docker"], 1, "", "PermissionError: confidential-marker\n")
        error = CommandFailure("docker", result)
        self.assertEqual(error.evidence["exception_class"], "PermissionError")
        self.assertNotIn("confidential-marker", json.dumps(error.evidence))
        self.assertNotIn("confidential-marker", str(error))

    def test_admin_rejects_non_synthetic_target_before_importing_app(self):
        with patch.dict("os.environ", {"CHOPLAB_SYNTHETIC_BUDGET": "yes",
                                     "CHOPLAB_DATABASE_URL": "postgresql://example.invalid/other"}, clear=True):
            with self.assertRaises(RuntimeError):
                admin.guard()

    def test_admin_accepts_only_known_throwaway_postgres(self):
        with patch.dict("os.environ", {"CHOPLAB_SYNTHETIC_BUDGET": "yes",
                                     "CHOPLAB_DATABASE_URL":
                                     "postgresql://choplab_test:synthetic-ci-password@postgres:5432/choplab_recovery_test"}, clear=True):
            admin.guard()


if __name__ == "__main__":
    unittest.main()
