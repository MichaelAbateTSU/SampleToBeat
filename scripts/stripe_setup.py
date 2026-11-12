"""Creates everything ChopLab needs in a Stripe account and prints the environment variables to set.

  STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py https://your-site.example

Creates (or reuses, so it is safe to run twice) the Pro and Studio products, their monthly and yearly prices,
and a webhook endpoint at <site>/api/billing/webhook. Start with a test-mode key (sk_test_...), try a payment
with card 4242 4242 4242 4242, then run it again with your live key.

Prices come from backend/src/choplab/accounts.py, the same table the app enforces."""
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
API = "https://api.stripe.com/v1"
EVENTS = ["checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
          "customer.subscription.deleted"]


def plan_prices() -> dict[str, dict]:
    from choplab.accounts import PLANS

    return {k: {"label": p["label"], "tagline": p["tagline"], "monthly": round(p["price_monthly"] * 100),
                "yearly": round(p["price_yearly"] * 100)} for k, p in PLANS.items() if p["price_monthly"] > 0}


def setup(key: str, site: str, client=httpx) -> dict[str, str]:
    def call(method: str, path: str, data: dict | None = None, params: dict | None = None) -> dict:
        r = client.request(method, f"{API}{path}", data=data, params=params, auth=(key, ""), timeout=30)
        body = r.json()
        if r.status_code >= 400:
            raise SystemExit(f"Stripe refused {method} {path}: {body.get('error', {}).get('message')}")
        return body

    env = {"STRIPE_SECRET_KEY": key}
    for plan, info in plan_prices().items():
        for interval, unit in (("monthly", "month"), ("yearly", "year")):
            lookup = f"choplab_{plan}_{interval}"
            found = call("GET", "/prices", params={"lookup_keys[]": lookup, "active": "true"})["data"]
            if found and found[0]["unit_amount"] == info[interval]:
                price = found[0]
            else:
                products = call("GET", "/products/search", params={"query": f"metadata['choplab_plan']:'{plan}' AND active:'true'"})["data"]
                product = products[0] if products else call("POST", "/products", {
                    "name": f"ChopLab {info['label']}", "description": info["tagline"], "metadata[choplab_plan]": plan})
                price = call("POST", "/prices", {
                    "product": product["id"], "currency": "usd", "unit_amount": str(info[interval]),
                    "recurring[interval]": unit, "lookup_key": lookup, "transfer_lookup_key": "true",
                    "nickname": f"{info['label']} {interval}"})
            env[f"STRIPE_PRICE_{plan.upper()}_{interval.upper()}"] = price["id"]

    url = f"{site.rstrip('/')}/api/billing/webhook"
    hooks = call("GET", "/webhook_endpoints", params={"limit": "100"})["data"]
    if any(h["url"] == url and h["status"] == "enabled" for h in hooks):
        env["STRIPE_WEBHOOK_SECRET"] = "(already registered: copy the signing secret from the Stripe dashboard, Developers > Webhooks)"
    else:
        data = {"url": url, "description": "ChopLab subscriptions"}
        data.update({f"enabled_events[{i}]": e for i, e in enumerate(EVENTS)})
        env["STRIPE_WEBHOOK_SECRET"] = call("POST", "/webhook_endpoints", data)["secret"]
    return env


if __name__ == "__main__":
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if len(sys.argv) != 2 or not key.startswith(("sk_test_", "sk_live_", "rk_")) or not sys.argv[1].startswith("https://"):
        raise SystemExit(__doc__)
    print(f"# {'LIVE' if 'live' in key else 'TEST'} mode. Set these on your host (Render: Environment tab):\n")
    for k, v in setup(key, sys.argv[1]).items():
        print(f"{k}={v if k != 'STRIPE_SECRET_KEY' else '(the key you just used)'}")
    print("\nAlso switch on the customer portal once: Stripe dashboard > Settings > Billing > Customer portal.")
