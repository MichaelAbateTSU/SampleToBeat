"""The hosted product rules: guests, accounts, tenant isolation, plan limits, billing webhooks."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from choplab import accounts, billing, jobs, store
from choplab.api import app


@pytest.fixture(autouse=True)
def hosted(monkeypatch):
    monkeypatch.delenv("CHOPLAB_SELF_HOSTED", raising=False)
    monkeypatch.setattr(accounts, "_hits", {})


def client():
    return TestClient(app)


def make_beat(c, **settings):
    r = c.post("/api/beats", data={"settings": json.dumps({"variations": 1, "bars": 12, "seed": 5, **settings})})
    assert r.status_code == 202, r.text
    j = jobs.wait(r.json()["job"]["id"], timeout=300)
    assert j["status"] == "completed", j.get("error_message")
    return r.json()["project"]["id"], j


def register(c, email="a@example.com", password="correct horse"):
    r = c.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def test_guest_makes_a_beat_then_needs_an_account_to_download():
    c = client()
    assert c.get("/api/me").json()["user"] is None
    assert c.get("/api/projects").json() == []
    pid, job = make_beat(c)
    me = c.get("/api/me").json()
    assert me["user"]["guest"] and me["usage"]["generations_used"] == 1
    assert job["result"]["opened"] and job["result"]["variation_ids"]
    p = c.get(f"/api/projects/{pid}").json()
    assert p["arrangement"] and "user_id" in p
    assert all("path" not in k for s in p["sources"] for k in s)  # no server paths leak
    v = c.get(f"/api/projects/{pid}/variations").json()[0]
    assert "preview_path" not in v and c.get(f"/api/variations/{v['id']}/audio").status_code == 200

    r = c.post(f"/api/projects/{pid}/export", json={"format": "mp3"})
    assert r.status_code == 401 and r.json()["code"] == "signup_required"

    me = register(c, "guest-upgrade@example.com")
    assert me["user"]["email"] == "guest-upgrade@example.com" and not me["user"]["guest"]
    assert [x["id"] for x in c.get("/api/projects").json()] == [pid]  # the guest's beat came along

    r = c.post(f"/api/projects/{pid}/export", json={"format": "wav24"})
    assert r.status_code == 402 and r.json()["plan_needed"] == "pro" and r.json()["feature"] == "formats"
    for extra in ("stems", "midi", "project_json"):
        assert c.post(f"/api/projects/{pid}/export", json={"format": "mp3", extra: True}).status_code == 402
    r = c.post(f"/api/projects/{pid}/export", json={"format": "mp3"})
    assert r.status_code == 202
    assert jobs.wait(r.json()["job"]["id"])["status"] == "completed"
    e = c.get(f"/api/exports/{r.json()['export_id']}").json()
    assert {f["kind"] for f in e["files"]} == {"mix", "license"} and all("path" not in f for f in e["files"])
    lic = c.get(f"/api/exports/{e['id']}/download?name={next(f['name'] for f in e['files'] if f['kind'] == 'license')}").text
    assert "PERSONAL LICENSE" in lic and "sample you uploaded" in lic


def test_other_users_cannot_see_or_touch_a_project():
    a, b = client(), client()
    pid, _ = make_beat(a)
    make_beat(b)
    p = a.get(f"/api/projects/{pid}").json()
    sid, vid = p["source_ids"][0], p["variation_id"]
    smp = a.get(f"/api/projects/{pid}/samples").json()[0]["id"]
    job = a.get(f"/api/projects/{pid}/jobs").json()[0]["id"]
    for url in (f"/api/projects/{pid}", f"/api/projects/{pid}/arrangement", f"/api/projects/{pid}/variations",
                f"/api/projects/{pid}/samples", f"/api/projects/{pid}/jobs", f"/api/sources/{sid}",
                f"/api/sources/{sid}/audio", f"/api/sources/{sid}/analysis", f"/api/variations/{vid}",
                f"/api/variations/{vid}/audio", f"/api/samples/{smp}/audio", f"/api/jobs/{job}", f"/api/jobs/{job}/events"):
        assert b.get(url).status_code == 404, url
        assert client().get(url).status_code == 401, url  # no session at all
    assert b.delete(f"/api/projects/{pid}").status_code == 404
    assert b.put(f"/api/projects/{pid}/arrangement", json=p["arrangement"]).status_code == 404
    assert b.post(f"/api/variations/{vid}/regenerate").status_code == 404
    assert pid not in [x["id"] for x in b.get("/api/projects").json()]
    # an arrangement cannot point its 808 at a file from someone else's project
    arr = {**p["arrangement"], "bass": {"mode": "sample", "sample_path": f"projects/{b.get('/api/projects').json()[0]['id']}/sources/x.wav"}}
    assert a.put(f"/api/projects/{pid}/arrangement", json=arr).status_code == 400


def test_auth_lifecycle(caplog):
    c = client()
    assert c.post("/api/auth/register", json={"email": "nope", "password": "longenough"}).status_code == 400
    assert c.post("/api/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 400
    register(c, "Life@Example.com", "first password")
    assert c.post("/api/auth/register", json={"email": "life@example.com", "password": "another one"}).status_code == 400
    user = accounts.by_email("life@example.com")
    assert user["password_hash"].startswith("scrypt$") and "first password" not in json.dumps(user)

    assert c.post("/api/auth/logout").json()["ok"] and c.get("/api/me").json()["user"] is None
    assert c.post("/api/auth/login", json={"email": "life@example.com", "password": "wrong password"}).status_code == 400
    assert c.post("/api/auth/login", json={"email": "life@example.com", "password": "first password"}).json()["user"]["email"]

    # logging in from a guest session keeps the guest's work
    g = client()
    pid, _ = make_beat(g)
    g.post("/api/auth/login", json={"email": "life@example.com", "password": "first password"})
    assert pid in [x["id"] for x in g.get("/api/projects").json()]

    # reset: the token is only ever in the email, works once, and logs other devices out
    with caplog.at_level("WARNING", logger="choplab"):
        assert client().post("/api/auth/forgot", json={"email": "life@example.com"}).json()["ok"]
        assert client().post("/api/auth/forgot", json={"email": "nobody@example.com"}).json()["ok"]
    token = caplog.text.split("/reset?token=")[1].split()[0]
    r = client()
    assert r.post("/api/auth/reset", json={"token": token, "password": "second password"}).status_code == 200
    assert r.post("/api/auth/reset", json={"token": token, "password": "third password"}).status_code == 400
    assert c.get("/api/me").json()["user"] is None
    assert c.post("/api/auth/login", json={"email": "life@example.com", "password": "second password"}).status_code == 200

    # deleting the account removes its data
    assert c.delete("/api/account").status_code == 204
    assert accounts.by_email("life@example.com") is None and store.get("project", pid) is None


def test_login_is_rate_limited():
    c = client()
    codes = [c.post("/api/auth/login", json={"email": "who@example.com", "password": "guessing 123"}).status_code for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[-1] == 429


def test_generation_limits(monkeypatch):
    c = client()
    monkeypatch.setattr(accounts, "GUEST_GENERATIONS", 1)
    pid, _ = make_beat(c)
    r = c.post(f"/api/projects/{pid}/generate", json={"variations": 1, "bars": 12})
    assert r.status_code == 401 and r.json()["code"] == "signup_required"
    register(c, "limits@example.com")
    monkeypatch.setitem(accounts.PLANS["free"], "generations_per_month", 2)
    monkeypatch.setitem(accounts.PLANS["free"], "projects", 2)
    assert c.get("/api/me").json()["usage"]["generations_left"] == 1
    r = c.post(f"/api/projects/{pid}/generate", json={"variations": 1, "bars": 12})
    assert r.status_code == 202 and jobs.wait(r.json()["id"])["status"] == "completed"
    r = c.post(f"/api/projects/{pid}/generate", json={"variations": 1, "bars": 12})
    assert r.status_code == 402 and r.json()["feature"] == "generations"
    assert c.post("/api/projects", json={"name": "two"}).status_code == 201
    assert c.post("/api/projects", json={"name": "three"}).status_code == 402


def test_cross_site_posts_are_refused():
    c = client()
    assert c.post("/api/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://testserver"}).status_code == 201
    assert c.get("/api/health").headers["x-content-type-options"] == "nosniff"


def _signed(event: dict, secret: str, ts: int | None = None) -> tuple[bytes, str]:
    payload = json.dumps(event).encode()
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={ts},v1={sig}"


def test_stripe_webhooks_drive_the_plan(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_m")
    monkeypatch.setenv("STRIPE_PRICE_STUDIO_YEARLY", "price_studio_y")
    c = client()
    uid = register(c, "payer@example.com")["user"]["id"]

    def send(event, **kw):
        payload, header = _signed(event, "whsec_test", **kw)
        return c.post("/api/billing/webhook", content=payload, headers={"stripe-signature": header})

    sub = lambda price, status: {"id": "sub_1", "customer": "cus_9", "status": status, "metadata": {"user_id": uid},
                                 "current_period_end": 1900000000, "items": {"data": [{"price": {"id": price}}]}}
    assert send({"id": "evt_1", "type": "checkout.session.completed",
                 "data": {"object": {"client_reference_id": uid, "customer": "cus_9", "subscription": "sub_1"}}}).status_code == 200
    assert c.get("/api/me").json()["user"]["plan"] == "free"  # paying is confirmed by the subscription event
    assert send({"id": "evt_2", "type": "customer.subscription.created", "data": {"object": sub("price_pro_m", "active")}}).json()["result"] == "plan=pro"
    me = c.get("/api/me").json()
    assert me["user"]["plan"] == "pro" and me["plan"]["stems"] and me["usage"]["generations_limit"] == 300
    assert send({"id": "evt_2", "type": "customer.subscription.created", "data": {"object": sub("price_pro_m", "active")}}).json()["result"] == "duplicate"
    assert send({"id": "evt_3", "type": "customer.subscription.updated", "data": {"object": sub("price_studio_y", "active")}}).json()["result"] == "plan=studio"
    # later events identify the user by Stripe customer only
    gone = {"id": "sub_1", "customer": "cus_9", "status": "canceled", "items": {"data": []}}
    assert send({"id": "evt_4", "type": "customer.subscription.deleted", "data": {"object": gone}}).json()["result"] == "plan=free"
    assert c.get("/api/me").json()["user"]["plan"] == "free"

    # forged, stale and unsigned deliveries change nothing
    payload, header = _signed({"id": "evt_5", "type": "customer.subscription.created", "data": {"object": sub("price_pro_m", "active")}}, "wrong")
    assert c.post("/api/billing/webhook", content=payload, headers={"stripe-signature": header}).status_code == 400
    assert send({"id": "evt_6", "type": "customer.subscription.created", "data": {"object": sub("price_pro_m", "active")}},
                ts=int(time.time()) - 3600).status_code == 400
    assert c.post("/api/billing/webhook", content=b"{}").status_code == 400
    assert c.get("/api/me").json()["user"]["plan"] == "free"


def test_checkout_builds_the_right_stripe_request(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_PRO_YEARLY", "price_pro_y")
    sent = {}

    def fake_post(url, data, auth, timeout):
        sent.update(url=url, data=data, auth=auth)

        class R:
            status_code = 200
            json = staticmethod(lambda: {"url": "https://checkout.stripe.test/session"})
        return R()

    monkeypatch.setattr(billing.httpx, "post", fake_post)
    c = client()
    c.post("/api/projects", json={"name": "x"})  # a guest session
    assert c.post("/api/billing/checkout", json={"plan": "pro", "interval": "yearly"}).status_code == 401
    uid = register(c, "buyer@example.com")["user"]["id"]
    r = c.post("/api/billing/checkout", json={"plan": "pro", "interval": "yearly"})
    assert r.json()["url"] == "https://checkout.stripe.test/session"
    assert sent["url"].endswith("/checkout/sessions") and sent["auth"] == ("sk_test_x", "")
    assert sent["data"]["line_items[0][price]"] == "price_pro_y" and sent["data"]["mode"] == "subscription"
    assert sent["data"]["client_reference_id"] == uid and sent["data"]["customer_email"] == "buyer@example.com"
    assert c.post("/api/billing/checkout", json={"plan": "studio"}).status_code == 503  # no price configured
    assert c.post("/api/billing/checkout", json={"plan": "enterprise"}).status_code == 422


def test_billing_off_and_mock(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    c = client()
    register(c, "mock@example.com")
    assert c.get("/api/plans").json()["billing"] == "off"
    assert c.post("/api/billing/checkout", json={"plan": "pro"}).status_code == 503
    assert c.get("/api/billing/mock-complete?plan=pro").status_code == 404
    monkeypatch.setenv("CHOPLAB_BILLING", "mock")
    url = c.post("/api/billing/checkout", json={"plan": "pro"}).json()["url"]
    assert c.get(url.replace(accounts.base_url(), ""), follow_redirects=False).status_code == 303
    assert c.get("/api/me").json()["user"]["plan"] == "pro"


def test_expired_guests_are_purged_with_their_uploads():
    from choplab import api

    c, keep = client(), client()
    pid = c.post("/api/projects", json={"name": "abandoned"}).json()["id"]
    kept = keep.post("/api/projects", json={"name": "active guest"}).json()["id"]
    reg = client()
    register(reg, "stays@example.com")
    uid = store.get("project", pid)["user_id"]
    for s in store.list_("session", uid):
        store.update("session", s["id"], expires=time.time() - 1)
    assert api.purge_expired() >= 1
    assert store.get("project", pid) is None and store.get("user", uid) is None
    assert store.get("project", kept) is not None and accounts.by_email("stays@example.com")
    assert c.get("/api/me").json()["user"] is None
