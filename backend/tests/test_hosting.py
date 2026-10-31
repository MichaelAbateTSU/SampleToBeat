import asyncio
import base64
import hashlib
import threading

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from choplab import api, audio_io, jobs, store
from choplab.hosting import Hosting, OwnerAccess

PASSWORD = "a-long-test-owner-password"
SALT = b"0123456789abcdef"


@pytest.fixture
def hosting():
    return Hosting("https://studio.example.com", "owner", SALT,
                   hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), SALT, 600_000), 600_000)


@pytest.fixture
def hosted(hosting, monkeypatch):
    monkeypatch.setattr(api, "HOSTING", hosting)
    wrapper = FastAPI()
    wrapper.add_middleware(OwnerAccess, config=hosting)
    wrapper.add_middleware(TrustedHostMiddleware, allowed_hosts=["studio.example.com"])
    wrapper.mount("/", api.app)
    with TestClient(wrapper, base_url=hosting.origin) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json", "/assets/studio.js", "/api/projects",
                                  "/api/sources/private/audio", "/api/exports/private/download",
                                  "/api/jobs/private/events"])
def test_private_surfaces_require_login(hosted, path):
    res = hosted.get(path)
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith("Basic ")


def test_health_is_anonymous_and_contains_no_project_data(hosted):
    res = hosted.get("/api/health")
    assert res.status_code == 200 and res.json()["ok"]
    assert set(res.json()) == {"ok", "version", "engine"}
    assert "no-store" in res.headers["cache-control"]


def test_login_and_cross_site_protection(hosted):
    auth = ("owner", PASSWORD)
    assert hosted.get("/api/projects", auth=auth).status_code == 200
    assert hosted.post("/api/projects", auth=auth, json={"name": "blocked"},
                       headers={"Origin": "https://attacker.example"}).status_code == 403
    assert hosted.get("/api/projects", auth=auth, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    res = hosted.post("/api/projects", auth=auth, json={"name": "hosted"},
                      headers={"Origin": "https://studio.example.com"})
    assert res.status_code == 201
    assert res.headers["x-frame-options"] == "DENY"
    pid = res.json()["id"]
    assert hosted.delete(f"/api/projects/{pid}", auth=auth).status_code == 204
    assert hosted.get("/api/projects", auth=auth, headers={"Host": "other.example"}).status_code == 400
    assert hosted.post("/api/exports/nope/reveal", auth=auth).status_code == 409


def test_wrong_login_is_rate_limited(hosted):
    assert hosted.get("/api/projects", auth=("owner", "wrong")).status_code == 401
    assert hosted.get("/api/projects", auth=("owner", "wrong")).status_code == 429


def test_plain_http_never_prompts_for_password(hosted):
    res = hosted.get("http://studio.example.com/api/projects")
    assert res.status_code == 400
    assert "www-authenticate" not in res.headers


def test_cached_login_fingerprint_does_not_authorize_other_credentials(hosted):
    assert hosted.get("/api/projects", auth=("owner", PASSWORD)).status_code == 200
    assert hosted.get("/api/projects").status_code == 401
    invalid = base64.b64encode(f"other:{PASSWORD}".encode()).decode()
    assert hosted.get("/api/projects", headers={"Authorization": f"Basic {invalid}"}).status_code in (401, 429)


def test_concurrent_first_login_uses_successful_cached_credential(hosting):
    async def check():
        boundary = OwnerAccess(api.app, config=hosting)
        async with AsyncClient(transport=ASGITransport(app=boundary), base_url=hosting.origin) as client:
            responses = await asyncio.gather(*[
                client.get("/api/projects", auth=("owner", PASSWORD)) for _ in range(8)
            ])
        assert all(r.status_code == 200 for r in responses)
    asyncio.run(check())


def test_hosted_config_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOPLAB_MODE", "hosted")
    monkeypatch.setenv("CHOPLAB_PUBLIC_URL", "http://studio.example.com")
    with pytest.raises(ValueError, match="https"):
        Hosting.from_env()
    monkeypatch.setenv("CHOPLAB_PUBLIC_URL", "https://studio.example.com")
    monkeypatch.delenv("CHOPLAB_PASSWORD_HASH_FILE", raising=False)
    with pytest.raises(ValueError, match="PASSWORD_HASH_FILE"):
        Hosting.from_env()
    secret = tmp_path / "hash"
    secret.write_text("not a password hash")
    monkeypatch.setenv("CHOPLAB_PASSWORD_HASH_FILE", str(secret))
    with pytest.raises(ValueError, match="Invalid password hash"):
        Hosting.from_env()
    digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), SALT, 600_000)
    secret.write_text(f"pbkdf2_sha256$600000${SALT.hex()}${digest.hex()}")
    assert Hosting.from_env().origin == "https://studio.example.com"


def test_upload_limit_and_partial_cleanup(hosted, monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD", 4)
    auth = ("owner", PASSWORD)
    pid = hosted.post("/api/projects", auth=auth, json={"name": "bounds"}).json()["id"]
    res = hosted.post(f"/api/projects/{pid}/sources", auth=auth,
                      files={"file": ("large.wav", b"12345", "audio/wav")})
    assert res.status_code == 413
    assert not list(api.DATA_DIR.rglob("*.part"))
    monkeypatch.setattr(api, "MAX_UPLOAD", 100)
    monkeypatch.setattr(api, "MIN_FREE", 1 << 60)
    res = hosted.post(f"/api/projects/{pid}/sources", auth=auth,
                      files={"file": ("small.wav", b"123", "audio/wav")})
    assert res.status_code == 507
    assert not list(api.DATA_DIR.rglob("*.part"))
    assert hosted.delete(f"/api/projects/{pid}", auth=auth).status_code == 204


def test_duration_rejected_before_allocating_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 5)
    monkeypatch.setattr(audio_io, "probe", lambda _: {"duration": 6, "sample_rate": 48000})
    monkeypatch.setattr(audio_io.sf, "read", lambda *a, **k: pytest.fail("Must reject before decode"))
    with pytest.raises(audio_io.AudioError, match="5 seconds"):
        audio_io.decode(tmp_path / "long.wav")


def test_compute_slot_is_shared_and_retryable(monkeypatch):
    monkeypatch.setattr(jobs, "_compute", threading.BoundedSemaphore(1))
    with jobs.compute_slot():
        with pytest.raises(jobs.Busy, match="busy"):
            with jobs.compute_slot():
                pytest.fail("No second compute should be admitted")
        with TestClient(api.app) as c:
            # A real CPU-backed API surface surfaces the same retryable status.
            monkeypatch.setattr(api, "_get", lambda *args: {
                "source_id": "src", "stems": ["mix"], "start": 0, "end": 1, "source_bpm": 100})
            monkeypatch.setattr(api.sampling, "resolve", lambda *args: ([], []))
            res = c.get("/api/samples/test/audio")
            assert res.status_code == 429 and res.headers["retry-after"] == "5"
    with jobs.compute_slot():
        pass


def test_job_queue_is_bounded_and_capacity_is_released(monkeypatch):
    class Pool:
        def submit(self, fn, *args):
            self.work = (fn, args)

    pool = Pool()
    monkeypatch.setattr(jobs, "_pending", threading.BoundedSemaphore(1))
    monkeypatch.setattr(jobs, "_pools", {"default": pool})
    monkeypatch.setitem(jobs._handlers, "hosting-test", (lambda ctx: {"ok": True}, "default"))
    job = jobs.submit("hosting-test", None, {})
    with pytest.raises(jobs.Busy, match="queue is full"):
        jobs.submit("hosting-test", None, {})
    fn, args = pool.work
    fn(*args)
    assert store.get("job", job["id"])["status"] == "completed"
    second = jobs.submit("hosting-test", None, {})
    fn, args = pool.work
    fn(*args)
    assert store.get("job", second["id"])["status"] == "completed"
    store.delete("job", job["id"])
    store.delete("job", second["id"])


def test_queue_rejection_does_not_leave_import_queued(monkeypatch):
    pending = threading.BoundedSemaphore(1)
    pending.acquire()
    monkeypatch.setattr(jobs, "_pending", pending)
    source = store.put("source", {"id": store.new_id("src"), "analysis_status": "queued"})
    with pytest.raises(jobs.Busy):
        jobs.submit("import", None, {"source_id": source["id"]})
    assert store.get("source", source["id"])["analysis_status"] == "failed"
    store.delete("source", source["id"])
