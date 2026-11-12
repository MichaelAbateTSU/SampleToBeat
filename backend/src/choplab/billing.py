"""Stripe subscriptions: hosted Checkout to subscribe, the customer portal to manage/cancel, and webhooks as
the single source of truth for a user's plan. Talks to the Stripe REST API directly (no SDK dependency).

Environment:
  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
  STRIPE_PRICE_PRO_MONTHLY, STRIPE_PRICE_PRO_YEARLY, STRIPE_PRICE_STUDIO_MONTHLY, STRIPE_PRICE_STUDIO_YEARLY
  CHOPLAB_BILLING=mock   development only: "checkout" upgrades instantly without Stripe."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import httpx

from . import accounts, store
from .core import log

API = "https://api.stripe.com/v1"
ACTIVE = {"active", "trialing", "past_due"}  # past_due keeps access while Stripe retries the card
TOLERANCE_S = 300


class BillingError(RuntimeError):
    pass


def mode() -> str:
    if os.environ.get("CHOPLAB_BILLING") == "mock":
        return "mock"
    return "stripe" if os.environ.get("STRIPE_SECRET_KEY") else "off"


def price_id(plan: str, interval: str) -> str | None:
    return os.environ.get(f"STRIPE_PRICE_{plan.upper()}_{interval.upper()}")


def _plan_for_price(pid: str | None) -> str | None:
    for plan in ("pro", "studio"):
        for interval in ("monthly", "yearly"):
            if pid and price_id(plan, interval) == pid:
                return plan
    return None


def _post(path: str, data: dict) -> dict:
    try:
        r = httpx.post(f"{API}{path}", data=data, auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=20)
    except httpx.HTTPError as e:
        raise BillingError(f"Could not reach the payment provider: {e}") from e
    body = r.json()
    if r.status_code >= 400:
        log.error("stripe %s -> %s %s", path, r.status_code, body.get("error", {}).get("message"))
        raise BillingError(body.get("error", {}).get("message") or "The payment provider rejected the request.")
    return body


def checkout_url(user: dict, plan: str, interval: str = "monthly") -> str:
    if plan not in ("pro", "studio") or interval not in ("monthly", "yearly"):
        raise ValueError("Unknown plan or billing interval.")
    base = accounts.base_url()
    m = mode()
    if m == "mock":
        return f"{base}/api/billing/mock-complete?plan={plan}"
    if m == "off":
        raise BillingError("Payments are not configured on this server yet.")
    price = price_id(plan, interval)
    if not price:
        raise BillingError(f"No Stripe price is configured for {plan} ({interval}).")
    data = {"mode": "subscription", "line_items[0][price]": price, "line_items[0][quantity]": "1",
            "success_url": f"{base}/app/account?upgraded=1", "cancel_url": f"{base}/pricing",
            "client_reference_id": user["id"], "allow_promotion_codes": "true",
            "subscription_data[metadata][user_id]": user["id"], "metadata[user_id]": user["id"]}
    if user.get("stripe_customer_id"):
        data["customer"] = user["stripe_customer_id"]
    else:
        data["customer_email"] = user["email"]
    return _post("/checkout/sessions", data)["url"]


def portal_url(user: dict) -> str:
    if mode() == "mock":
        return f"{accounts.base_url()}/api/billing/mock-complete?plan=free"
    if mode() == "off" or not user.get("stripe_customer_id"):
        raise BillingError("There is no subscription to manage.")
    return _post("/billing_portal/sessions", {"customer": user["stripe_customer_id"],
                                              "return_url": f"{accounts.base_url()}/app/account"})["url"]


def cancel_subscription(user: dict) -> None:
    """Used when an account is deleted: stop billing immediately."""
    sub = user.get("stripe_subscription_id")
    if mode() == "stripe" and sub:
        try:
            httpx.delete(f"{API}/subscriptions/{sub}", auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=20)
        except httpx.HTTPError as e:
            raise BillingError(f"Could not cancel the subscription: {e}") from e


# ---------------------------------------------------------------- webhooks

def verify(payload: bytes, header: str | None, secret: str | None = None, now: float | None = None) -> dict:
    """Checks the Stripe-Signature header (HMAC-SHA256 over "<timestamp>.<body>") and returns the event."""
    secret = secret or os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise BillingError("Webhook secret is not configured.")
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    ts = next((v for k, v in parts if k == "t"), None)
    sigs = [v for k, v in parts if k == "v1"]
    if not ts or not ts.isdigit() or not sigs:
        raise ValueError("Malformed signature header.")
    expected = hmac.new(secret.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise ValueError("Signature does not match.")
    if abs((now or time.time()) - int(ts)) > TOLERANCE_S:
        raise ValueError("Signature timestamp is too old.")
    return json.loads(payload)


def _link_customer(user_id: str, customer: str | None) -> None:
    if customer:
        store.put("customer", {"id": f"cus_ref_{customer}", "user_id": user_id})
        store.update("user", user_id, stripe_customer_id=customer)


def _user_for(obj: dict) -> dict | None:
    uid = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
    if not uid and obj.get("customer"):
        ref = store.get("customer", f"cus_ref_{obj['customer']}")
        uid = ref["user_id"] if ref else None
    return store.get("user", uid) if uid else None


def set_plan(user_id: str, plan: str, **fields) -> dict:
    return store.update("user", user_id, plan=plan if plan in accounts.PLANS else "free", **fields)


def handle(event: dict) -> str:
    """Applies one webhook event. Idempotent: Stripe retries and may deliver out of order."""
    eid = event.get("id") or ""
    if eid and store.get("stripe_event", eid):
        return "duplicate"
    obj = (event.get("data") or {}).get("object") or {}
    kind = event.get("type", "")
    result = "ignored"
    user = _user_for(obj)
    if kind == "checkout.session.completed" and user:
        _link_customer(user["id"], obj.get("customer"))
        store.update("user", user["id"], stripe_subscription_id=obj.get("subscription"))
        result = "linked"  # the plan itself arrives with customer.subscription.created/updated
    elif kind in ("customer.subscription.created", "customer.subscription.updated") and user:
        _link_customer(user["id"], obj.get("customer"))
        items = ((obj.get("items") or {}).get("data") or [{}])
        plan = _plan_for_price(((items[0].get("price") or {}).get("id")))
        status = obj.get("status")
        fields = {"stripe_subscription_id": obj.get("id"), "subscription_status": status,
                  "current_period_end": obj.get("current_period_end") or items[0].get("current_period_end"),
                  "cancel_at_period_end": bool(obj.get("cancel_at_period_end"))}
        set_plan(user["id"], plan if (plan and status in ACTIVE) else "free", **fields)
        result = f"plan={plan if (plan and status in ACTIVE) else 'free'}"
    elif kind == "customer.subscription.deleted" and user:
        set_plan(user["id"], "free", subscription_status="canceled", stripe_subscription_id=None,
                 cancel_at_period_end=False)
        result = "plan=free"
    if eid:
        store.put("stripe_event", {"id": eid, "type": kind, "result": result})
    return result
