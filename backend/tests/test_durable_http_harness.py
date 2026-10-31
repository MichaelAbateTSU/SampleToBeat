"""Safety and deterministic failure tests for the external HTTP acceptance CLI."""
import importlib.util
import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "durable_http", Path(__file__).resolve().parents[2] / "scripts" / "verify_durable_http.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


@pytest.fixture
def auth(tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"username": "owner", "password": "secret-never-log"}))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("origin,expected,loopback", [
    ("https://user:secret@test.invalid", "https://user:secret@test.invalid", False),
    ("https://test.invalid/path", "https://test.invalid/path", False),
    ("https://test.invalid?secret=value", "https://test.invalid?secret=value", False),
    ("https://test.invalid/", "https://test.invalid/", False),
    ("https://test.invalid", "https://different.invalid", False),
    ("http://test.invalid", "http://test.invalid", True),
    ("http://localhost:8765", "http://localhost:8765", False),
])
def test_unsafe_origins_rejected_before_network(auth, origin, expected, loopback):
    with pytest.raises(harness.AcceptanceError):
        harness.Client(origin, expected, auth, allow_loopback_http=loopback)


def test_opt_in_and_separate_private_manifest_required(auth, tmp_path, capsys):
    args = ["prepare", "--base-url", "https://test.invalid", "--expected-origin", "https://test.invalid",
            "--auth-file", str(auth), "--manifest", str(tmp_path / "manifest.json")]
    assert harness.main(args) == 1
    assert "opt-in" in capsys.readouterr().out
    args[-1] = str(auth)
    assert harness.main(args + ["--confirm-synthetic-test"]) == 1
    assert "overwrite" in capsys.readouterr().out
    assert "secret-never-log" in auth.read_text()


def test_delete_and_cleanup_are_never_allowed(auth):
    client = harness.Client("https://test.invalid", "https://test.invalid", auth)
    with pytest.raises(harness.AcceptanceError, match="destructive"):
        client.open("DELETE", "/api/projects/real")
    with pytest.raises(harness.AcceptanceError, match="forbidden"):
        client.open("POST", "/api/system/storage/reconcile")
    with pytest.raises(harness.AcceptanceError, match="forbidden"):
        client.open("POST", "/api/kits/trap/kick")


class PreflightClient:
    base = "https://test.invalid"
    def __init__(self, projects=None, blocked=False):
        self.projects, self.blocked = projects or [], blocked
        self.mutations = []
    def json(self, method, path, body=None):
        if method != "GET":
            self.mutations.append((method, path))
            raise AssertionError("Unexpected mutation")
        return {
            "/api/health": {"ok": True},
            "/api/system/capabilities": {"hosted": True, "metadata_store": "postgres", "job_recovery_error": "blocked" if self.blocked else None},
            "/api/system/recovery": {"ok": True, "read_only": False},
            "/api/projects": self.projects,
        }[path]
    def open(self, *args, **kwargs):
        raise harness.AcceptanceError("Private authentication rejected (HTTP 401)")


@pytest.mark.parametrize("blocked", [False, True])
def test_real_library_and_read_only_state_reject_prepare_before_mutation(tmp_path, blocked):
    client = PreflightClient([{"id": "real-project"}], blocked)
    with pytest.raises(harness.AcceptanceError, match="read-only|EMPTY"):
        harness.prepare(client, tmp_path / "manifest.json")
    assert not client.mutations
    assert not (tmp_path / "manifest.json").exists()


def test_existing_manifest_does_not_duplicate_creation(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{}")
    with pytest.raises(harness.AcceptanceError, match="already exists"):
        harness.prepare(PreflightClient(), path)


def test_partial_prepare_preserves_nonsecret_ids_and_cannot_be_verified(tmp_path):
    class Partial(PreflightClient):
        def json(self, method, path, body=None):
            if method == "POST" and path == "/api/projects":
                return {"id": "prj_synthetic"}
            return super().json(method, path, body)
        def upload(self, *args):
            raise harness.AcceptanceError("HTTP request timed out")
    client = Partial()
    path = tmp_path / "manifest.json"
    with pytest.raises(harness.AcceptanceError, match="timed out"):
        harness.prepare(client, path)
    saved = json.loads(path.read_text())
    assert saved["complete"] is False and saved["ids"] == {"project": "prj_synthetic"}
    with pytest.raises(harness.AcceptanceError, match="incomplete"):
        harness.verify(client, path)
    # Do not delete or overwrite another operation's existing temporary file.
    other = tmp_path / "manifest.json.part"
    other.write_text("unrelated")
    harness.save(path, saved)
    assert other.read_text() == "unrelated"


def test_synthetic_source_is_short_deterministic_stereo_pcm():
    import wave
    audio = harness.synthetic_audio()
    assert audio == harness.synthetic_audio()
    with wave.open(io.BytesIO(audio)) as reader:
        assert reader.getnchannels() == 2 and reader.getsampwidth() == 2
        assert 10 < reader.getnframes() / reader.getframerate() < 12
        assert any(reader.readframes(reader.getnframes()))


@pytest.mark.parametrize("status", ["failed", "interrupted", "canceled", "invented"])
def test_failed_or_unknown_jobs_never_report_completion(auth, monkeypatch, status):
    client = harness.Client("https://test.invalid", "https://test.invalid", auth)
    monkeypatch.setattr(client, "json", lambda *args, **kwargs: {"status": status})
    with pytest.raises(harness.AcceptanceError):
        client.wait({"id": "job_test"})


def test_job_timeout_is_deterministic_and_does_not_retry(auth, monkeypatch):
    client = harness.Client("https://test.invalid", "https://test.invalid", auth, job_timeout=1)
    clock = [0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(client, "json", lambda *args, **kwargs: {"status": "running"})
    with pytest.raises(harness.AcceptanceError, match="observation timeout"):
        client.wait({"id": "job_test"})


def test_metadata_checksum_ignores_cache_root_but_not_results():
    old = {"analysis": {"bpm": 92}, "internal_audio_path": "/old/audio.wav", "updated_at": 1}
    new = {**old, "internal_audio_path": "/new/audio.wav", "updated_at": 2}
    assert harness.metadata_digest(old) == harness.metadata_digest(new)
    new["analysis"] = {"bpm": 94}
    assert harness.metadata_digest(old) != harness.metadata_digest(new)


@pytest.fixture
def http_server():
    state = {"requests": [], "status": 200, "body": b"{}"}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Authorization"), self.headers.get("X-Forwarded-Proto")))
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", "/api/credential-trap")
            if state["status"] == 206:
                self.send_header("Content-Range", state.get("range", "bytes 0-63/100"))
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("status", [302, 401, 403, 404, 409, 503])
def test_http_errors_and_redirect_never_log_bodies_or_forward_credentials(http_server, auth, status):
    base, state = http_server
    state.update(status=status, body=b"secret-never-log")
    client = harness.Client(base, base, auth, allow_loopback_http=True)
    with pytest.raises(harness.AcceptanceError) as error:
        client.json("GET", "/api/projects")
    assert "secret-never-log" not in str(error.value)
    assert len(state["requests"]) == 1
    assert state["requests"][0][1].startswith("Basic ")


def test_range_integrity_and_download_limits(http_server, auth, monkeypatch):
    base, state = http_server
    client = harness.Client(base, base, auth, allow_loopback_http=True)
    state.update(status=206, body=b"x" * 64)
    assert client.checksum("/api/audio", expected_status=206) == harness.digest(b"x" * 64)
    state["range"] = "bytes 1-64/100"
    with pytest.raises(harness.AcceptanceError, match="boundary"):
        client.checksum("/api/audio", expected_status=206)
    state.update(status=200, body=b"x" * 100)
    monkeypatch.setattr(harness, "MAX_ASSET", 80)
    with pytest.raises(harness.AcceptanceError, match="safety bound"):
        client.checksum("/api/audio")


def test_anonymous_loopback_probe_omits_auth_but_preserves_https_proxy_scheme(http_server, auth):
    base, state = http_server
    client = harness.Client(base, base, auth, allow_loopback_http=True)
    with client.open("GET", "/api/projects", anonymous=True):
        pass
    assert state["requests"] == [("/api/projects", None, "https")]


def test_network_timeouts_are_safe(auth, monkeypatch):
    client = harness.Client("https://test.invalid", "https://test.invalid", auth)
    monkeypatch.setattr(client.opener, "open", lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout("secret-never-log")))
    with pytest.raises(harness.AcceptanceError, match="timed out") as error:
        client.json("GET", "/api/health")
    assert "secret-never-log" not in str(error.value)


def test_trickling_metadata_cannot_bypass_observation_deadline(auth, monkeypatch):
    client = harness.Client("https://test.invalid", "https://test.invalid", auth, timeout=1)
    clock = [0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])
    class Slow:
        def read1(self, size):
            clock[0] += .6
            return b" "
    with pytest.raises(harness.AcceptanceError, match="observation timeout"):
        client._read_metadata(Slow(), 1)
    assert clock[0] < 2


def manifest(client):
    return {"format": harness.FORMAT, "complete": True, "run_id": "a" * 32,
            "origin_sha256": harness.digest(client.base.encode()),
            "ids": {key: key + "_synthetic" for key in ("project", "source", "variation", "preview", "export", "import_job", "generate_job", "export_job")},
            "sha256": {key: "a" * 64 for key in harness.CHECK_NAMES}}


@pytest.mark.parametrize("failure", ["incomplete", "origin", "id", "checksum", "unexpected-key"])
def test_bad_manifest_rejected_before_verification_network(tmp_path, failure):
    client = PreflightClient()
    data = manifest(client)
    if failure == "incomplete":
        data["complete"] = False
    elif failure == "origin":
        data["origin_sha256"] = "b" * 64
    elif failure == "id":
        data["ids"]["project"] = "../real"
    elif failure == "checksum":
        data["sha256"]["source_audio"] = "not-sha"
    else:
        data["sha256"]["secret-never-log"] = data["sha256"].pop("source_audio")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    with pytest.raises(harness.AcceptanceError):
        harness.verify(client, path)
    assert not client.mutations


def test_verify_is_read_only_and_checksum_mismatch_is_explicit(tmp_path, monkeypatch):
    client = PreflightClient()
    data = manifest(client)
    old_json = client.json
    def respond(method, path, body=None):
        assert method == "GET"
        if path.startswith("/api/projects/"):
            return {"name": "synthetic-" + data["run_id"]}
        if path.startswith("/api/jobs/"):
            return {"status": "completed"}
        return old_json(method, path, body)
    monkeypatch.setattr(client, "json", respond)
    monkeypatch.setattr(harness, "checksums", lambda *args: dict(data["sha256"]))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    saved = path.read_bytes()
    harness.verify(client, path)
    harness.verify(client, path)
    assert not client.mutations and path.read_bytes() == saved
    observed = {**data["sha256"], "source_audio": "b" * 64}
    monkeypatch.setattr(harness, "checksums", lambda *args: observed)
    with pytest.raises(harness.AcceptanceError, match="checksum mismatch: source_audio"):
        harness.verify(client, path)


def test_verification_uses_recorded_upload_hash_not_platform_dependent_regeneration(monkeypatch):
    client = PreflightClient()
    data = manifest(client)
    def respond(method, path, body=None):
        assert method == "GET"
        if path == "/api/sources/source_synthetic":
            return {"analysis_status": "analyzed", "content_hash": "a" * 64}
        if path == "/api/exports/export_synthetic":
            return {"status": "done", "files": [{"name": "synthetic.wav", "check": {"silent": False}}]}
        return {}
    monkeypatch.setattr(client, "json", respond)
    monkeypatch.setattr(client, "checksum", lambda *args, **kwargs: "a" * 64, raising=False)
    monkeypatch.setattr(harness, "synthetic_audio", lambda: (_ for _ in ()).throw(AssertionError("Do not regenerate in verify")))
    assert harness.checksums(client, data)["original_upload"] == "a" * 64
