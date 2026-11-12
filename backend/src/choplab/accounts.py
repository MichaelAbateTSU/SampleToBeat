"""Accounts, sessions, plans and usage metering.

Visitors start as *guests* (a cookie session, no email) so they can hear a beat made from their own sample
before being asked for anything. Registering keeps the same user id, so nothing they made is lost.
What each plan may do lives in PLANS; the API enforces it server-side and the UI renders it from /api/plans."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage

from . import store
from .core import log

SESSION_COOKIE = "choplab_session"
SESSION_DAYS = 30
RESET_MINUTES = 60
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")

PLANS = {
    "free": {
        "label": "Free", "tagline": "Hear what your sample can become", "price_monthly": 0, "price_yearly": 0,
        "generations_per_month": 15, "projects": 5, "max_upload_mb": 50, "max_source_minutes": 3,
        "formats": ["mp3"], "stems": False, "midi": False, "project_file": False, "separation": False,
        "commercial_license": False,
    },
    "pro": {
        "label": "Pro", "tagline": "Release-ready beats", "price_monthly": 9.99, "price_yearly": 95.88,
        "generations_per_month": 300, "projects": 200, "max_upload_mb": 200, "max_source_minutes": 5,
        "formats": ["mp3", "wav16", "wav24"], "stems": True, "midi": True, "project_file": True, "separation": True,
        "commercial_license": True,
    },
    "studio": {
        "label": "Studio", "tagline": "For producers who ship every week", "price_monthly": 24.99, "price_yearly": 239.88,
        "generations_per_month": 2000, "projects": 2000, "max_upload_mb": 200, "max_source_minutes": 5,
        "formats": ["mp3", "wav16", "wav24", "wav32f"], "stems": True, "midi": True, "project_file": True,
        "separation": True, "commercial_license": True,
    },
}
# Upload length is capped by server memory, measured on a full four-flip run: about 1.1 GB up to 3 minutes,
# 1.4 GB at 6, 2.6 GB at 12. The launch server has 2 GB, so 5 minutes is the ceiling until it is upsized.
GUEST_GENERATIONS = 3  # lifetime, before an account is required
PLAN_ORDER = ["free", "pro", "studio"]


class Paywall(Exception):
    """The request is valid but the user's plan does not include it (HTTP 402)."""

    def __init__(self, message: str, feature: str, plan_needed: str = "pro"):
        super().__init__(message)
        self.feature, self.plan_needed = feature, plan_needed


class SignupRequired(Exception):
    """A guest tried something that needs a real account (HTTP 401, code signup_required)."""


class AuthError(ValueError):
    pass


# ---------------------------------------------------------------- configuration (read at call time)

def self_hosted() -> bool:
    """One trusted user on their own machine: everything unlocked, no sign-up wall."""
    return os.environ.get("CHOPLAB_SELF_HOSTED", "") == "1"


def default_plan() -> str:
    return "studio" if self_hosted() else "free"


def base_url() -> str:
    """Public URL of the site. Falls back to the URL Render assigns, then to local development."""
    return (os.environ.get("CHOPLAB_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8765").rstrip("/")


def cookie_secure() -> bool:
    return base_url().startswith("https://")


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    if len(password) < 8:
        raise AuthError("Use a password of at least 8 characters.")
    if len(password) > 256:
        raise AuthError("Password is too long.")
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$14$8$1${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, h = (stored or "").split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- users

def _email_key(email: str) -> str:
    return "eml_" + hashlib.sha256(email.encode()).hexdigest()[:32]


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise AuthError("Enter a valid email address.")
    return e


def create_guest() -> dict:
    return store.put("user", {"id": store.new_id("usr"), "email": None, "password_hash": None, "guest": True,
                              "plan": default_plan(), "usage": {}, "lifetime_generations": 0,
                              "stripe_customer_id": None, "subscription_status": None})


def by_email(email: str) -> dict | None:
    ref = store.get("email", _email_key(email))
    return store.get("user", ref["user_id"]) if ref else None


def register(user: dict | None, email: str, password: str) -> dict:
    """Turns the current guest into a registered user (or creates one), keeping their projects."""
    email = normalize_email(email)
    pw = hash_password(password)
    with store._lock:
        if store.get("email", _email_key(email)):
            raise AuthError("An account with this email already exists. Log in instead.")
        if user is None or not user.get("guest"):
            user = create_guest()
        store.put("email", {"id": _email_key(email), "user_id": user["id"]})
        return store.update("user", user["id"], email=email, password_hash=pw, guest=False, registered_at=store.now())


def authenticate(email: str, password: str) -> dict:
    user = by_email(normalize_email(email))
    # always burn a hash so response time does not reveal whether the email exists
    ok = check_password(password, user["password_hash"] if user else "scrypt$14$8$1$00$00")
    if not user or not ok:
        raise AuthError("Wrong email or password.")
    return user


def public(user: dict) -> dict:
    return {"id": user["id"], "email": user.get("email"), "guest": bool(user.get("guest")), "plan": plan_id(user),
            "subscription_status": user.get("subscription_status"), "current_period_end": user.get("current_period_end"),
            "cancel_at_period_end": bool(user.get("cancel_at_period_end"))}


# ---------------------------------------------------------------- sessions

def _hash(token: str) -> str:
    return "ses_" + hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    store.put("session", {"id": _hash(token), "user_id": user_id, "expires": time.time() + SESSION_DAYS * 86400},
              parent=user_id)
    return token  # only the hash is stored: a database leak does not leak live sessions


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    s = store.get("session", _hash(token))
    if not s:
        return None
    if s["expires"] < time.time():
        store.delete("session", s["id"])
        return None
    return store.get("user", s["user_id"])


def end_session(token: str | None) -> None:
    if token:
        store.delete("session", _hash(token))


def end_all_sessions(user_id: str) -> None:
    for s in store.list_("session", user_id):
        store.delete("session", s["id"])


# ---------------------------------------------------------------- password reset

def request_reset(email: str) -> None:
    """Never reveals whether the address has an account."""
    try:
        user = by_email(normalize_email(email))
    except AuthError:
        return
    if not user:
        return
    token = secrets.token_urlsafe(32)
    store.put("reset", {"id": "rst_" + hashlib.sha256(token.encode()).hexdigest(), "user_id": user["id"],
                        "expires": time.time() + RESET_MINUTES * 60}, parent=user["id"])
    link = f"{base_url()}/reset?token={token}"
    send_email(user["email"], "Reset your ChopLab password",
               f"Someone asked to reset the password for this ChopLab account.\n\n{link}\n\n"
               f"The link works once and expires in {RESET_MINUTES} minutes. If this wasn't you, ignore this email.")


def reset_password(token: str, password: str) -> dict:
    rid = "rst_" + hashlib.sha256((token or "").encode()).hexdigest()
    r = store.get("reset", rid)
    if not r or r["expires"] < time.time():
        raise AuthError("This reset link is invalid or has expired. Request a new one.")
    pw = hash_password(password)
    store.delete("reset", rid)
    end_all_sessions(r["user_id"])  # a reset logs out every device
    return store.update("user", r["user_id"], password_hash=pw)


def send_email(to: str, subject: str, body: str) -> bool:
    """SMTP when CHOPLAB_SMTP_HOST is set; otherwise the message is logged (development)."""
    host = os.environ.get("CHOPLAB_SMTP_HOST")
    if not host:
        log.warning("email not configured (set CHOPLAB_SMTP_HOST); would send to %s: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = os.environ.get("CHOPLAB_MAIL_FROM", "ChopLab <no-reply@localhost>")
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("CHOPLAB_SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            if os.environ.get("CHOPLAB_SMTP_USER"):
                s.login(os.environ["CHOPLAB_SMTP_USER"], os.environ.get("CHOPLAB_SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.error("could not send email to %s: %s", to, e)
        return False


# ---------------------------------------------------------------- plans and usage

def plan_id(user: dict) -> str:
    if self_hosted():
        return "studio"
    p = user.get("plan") or "free"
    return p if p in PLANS else "free"


def plan(user: dict) -> dict:
    return PLANS[plan_id(user)]


def _month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def usage(user: dict) -> dict:
    p = plan(user)
    used = int((user.get("usage") or {}).get(_month(), 0))
    limit = p["generations_per_month"]
    if user.get("guest") and not self_hosted():
        used, limit = int(user.get("lifetime_generations") or 0), GUEST_GENERATIONS
    return {"generations_used": used, "generations_limit": limit, "generations_left": max(0, limit - used),
            "period": "lifetime" if user.get("guest") and not self_hosted() else _month(),
            "projects_limit": p["projects"]}


def check_generation(user: dict) -> None:
    u = usage(user)
    if u["generations_left"] > 0:
        return
    if user.get("guest"):
        raise SignupRequired("Create a free account to keep making beats.")
    nxt = PLAN_ORDER[min(len(PLAN_ORDER) - 1, PLAN_ORDER.index(plan_id(user)) + 1)]
    raise Paywall(f"You've used all {u['generations_limit']} generations on the {plan(user)['label']} plan this month.",
                  "generations", nxt)


def count_generation(user_id: str) -> None:
    with store._lock:
        user = store.get("user", user_id)
        if not user:
            return
        m = _month()
        store.update("user", user_id, usage={m: int((user.get("usage") or {}).get(m, 0)) + 1},  # old months dropped
                     lifetime_generations=int(user.get("lifetime_generations") or 0) + 1)


def require(user: dict, feature: str, value=None) -> None:
    """Raises Paywall unless the user's plan includes `feature` (and, for list features, `value`)."""
    p = plan(user)
    have = p.get(feature)
    ok = (value in have) if isinstance(have, list) else bool(have)
    if ok:
        return
    needed = next((k for k in PLAN_ORDER if (value in PLANS[k][feature] if isinstance(PLANS[k][feature], list)
                                             else PLANS[k][feature])), "studio")
    names = {"stems": "Stem export", "midi": "MIDI export", "project_file": "Project file export",
             "separation": "Stem separation", "formats": f"{str(value).upper()} export"}
    raise Paywall(f"{names.get(feature, feature)} is part of the {PLANS[needed]['label']} plan.", feature, needed)


def license_text(user: dict, beat_name: str) -> str:
    p = plan(user)
    who = user.get("email") or "guest"
    when = time.strftime("%Y-%m-%d", time.gmtime())
    if p["commercial_license"]:
        grant = ("COMMERCIAL LICENSE. You may release, sell, monetize and perform this beat and songs made with it,\n"
                 "worldwide and in perpetuity, with no royalties owed to ChopLab. No attribution is required.")
    else:
        grant = ("PERSONAL LICENSE. You may use this beat for non-commercial purposes: demos, practice, and\n"
                 "non-monetized posts. Upgrade to Pro for a commercial license covering releases and monetization.")
    return (f"ChopLab beat license\nBeat: {beat_name}\nLicensee: {who}\nPlan: {p['label']}\nIssued: {when}\n\n{grant}\n\n"
            "WHAT THIS COVERS. Everything ChopLab generated: the drum programming, drum sounds, 808/bass line,\n"
            "arrangement and mixdown.\n\n"
            "WHAT THIS DOES NOT COVER. The sample you uploaded. ChopLab does not grant, clear or verify rights in\n"
            "your source audio. If the sample contains someone else's recording or composition, you need\n"
            "permission from the rights holders before releasing music that uses it.\n")


# ---------------------------------------------------------------- rate limiting (per process)

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def rate_limit(key: str, limit: int, window_s: float) -> bool:
    """True if the call is allowed. In-memory sliding window: enough for one node; use the proxy's limiter beyond that."""
    now = time.time()
    with _hits_lock:
        h = [t for t in _hits.get(key, []) if now - t < window_s]
        if len(h) >= limit:
            _hits[key] = h
            return False
        h.append(now)
        _hits[key] = h
        if len(_hits) > 50000:
            _hits.clear()
        return True
