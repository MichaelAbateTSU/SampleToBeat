"""Actual protected HTTP phases against disposable PostgreSQL/S3, never providers."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_durable import remote, worker_env  # Reuse guarded disposable service fixture.
from choplab import store


def test_two_phase_http_harness_after_destroyed_cache(remote):
    if os.environ.get("CHOPLAB_FULL_DURABLE_AUDIO") != "yes":
        pytest.skip("Actual HTTP DSP acceptance runs only in the separate uncapped CI step")
    root, _, _ = remote
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    auth = root.parent / "private-http-auth.json"
    auth.write_text(json.dumps({"username": "owner", "password": "synthetic-http-only"}))
    auth.chmod(0o600)
    salt = b"synthetic-http-test-salt"
    hashed = hashlib.pbkdf2_hmac("sha256", b"synthetic-http-only", salt, 600000)
    secret = root.parent / "private-http-hash"
    secret.write_text(f"pbkdf2_sha256$600000${salt.hex()}${hashed.hex()}")
    secret.chmod(0o600)
    manifest = root.parent / "http-manifest.json"
    # Reserve a listening socket and pass it into uvicorn; no port probing race.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    env = {**worker_env(remote, root), "CHOPLAB_MODE": "hosted",
           "CHOPLAB_PUBLIC_URL": "https://localhost", "CHOPLAB_PASSWORD_HASH_FILE": str(secret)}
    command = [sys.executable, str(scripts / "verify_durable_http.py")]
    flags = ["--base-url", base, "--expected-origin", base, "--auth-file", str(auth),
             "--manifest", str(manifest), "--confirm-synthetic-test", "--allow-loopback-http",
             "--job-timeout", "300"]
    log = root.parent / "synthetic-http-server.log"
    def start(cache):
        output = log.open("ab")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "choplab.api:app", "--fd", str(listener.fileno()),
             "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", "--no-access-log"],
            env={**env, "CHOPLAB_DATA": str(cache)}, pass_fds=(listener.fileno(),),
            stdout=output, stderr=output)
        output.close()
        from urllib.request import urlopen
        from urllib.error import URLError
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, "Disposable HTTP process stopped; inspect its private test log."
            try:
                with urlopen(base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        return process
            except (URLError, TimeoutError):
                time.sleep(.1)
        process.kill()
        process.wait(timeout=10)
        pytest.fail("Disposable HTTP process failed readiness deadline.")
    def run(phase, expected=0):
        result = subprocess.run(command + [phase] + flags, capture_output=True, text=True, timeout=360)
        assert result.returncode == expected, result.stdout
        assert "synthetic-http-only" not in result.stdout + result.stderr
        return result
    process = None
    try:
        process = start(root)
        run("prepare")
        saved = manifest.read_bytes()
        data = json.loads(saved)
        assert data["complete"] and len(data["sha256"]) == 13
        assert b"synthetic-http-only" not in saved and str(root).encode() not in saved
        assert b"Authorization" not in saved
        # A duplicate prepare fails without creating a second project.
        run("prepare", expected=1)
        with store._conn() as con:
            assert con.execute("SELECT COUNT(*) FROM docs WHERE kind='project'").fetchone()[0] == 1
        # Abrupt HTTP process termination and actual deletion of its cache.
        process.kill()
        process.wait(timeout=10)
        process = None
        shutil.rmtree(root)
        assert not root.exists()
        with store._conn() as con:
            con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
        replacement = root.parent / "http-replacement"
        process = start(replacement)
        run("verify")
        run("verify")
        assert manifest.read_bytes() == saved
        # A deliberate synthetic checksum mismatch must fail, never become success.
        data["sha256"]["source_audio"] = "0" * 64
        manifest.write_text(json.dumps(data))
        failed = run("verify", expected=1)
        assert "checksum mismatch" in failed.stdout
        manifest.write_bytes(saved)
    finally:
        if process is not None:
            process.kill()
            process.wait(timeout=10)
        listener.close()
