"""Deploy the Discord command Worker to Cloudflare.

Uploads worker/discord-worker.js, sets its three secrets, and enables the
workers.dev URL — all through Cloudflare's API, so there's no dashboard
clicking and re-running it is safe.

Needs these in .env:
    CF_API_TOKEN        Cloudflare token with Workers Scripts: Edit
    DISCORD_PUBLIC_KEY  from the Discord developer portal
    GH_DISPATCH_TOKEN   GitHub PAT that can trigger repository_dispatch
    GH_REPO             kapsretrorescue/kaps-deal-finder

    .venv\\Scripts\\python.exe deploy_worker.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
API = "https://api.cloudflare.com/client/v4"
SCRIPT = "kaps-commands"


def need(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"{name} missing from .env")
    return v


def main() -> None:
    token = need("CF_API_TOKEN")
    h = {"Authorization": f"Bearer {token}"}

    accounts = requests.get(f"{API}/accounts", headers=h, timeout=30).json()
    if not accounts.get("success"):
        raise SystemExit(f"token rejected: {accounts}")
    acct = accounts["result"][0]
    aid = acct["id"]
    print(f"Account: {acct['name']}")

    code = (ROOT / "worker" / "discord-worker.js").read_text(encoding="utf-8")

    # Upload as an ES module worker. Secrets are declared as bindings so they
    # arrive with the very first request — no separate secret round-trip.
    metadata = {
        "main_module": "worker.js",
        "compatibility_date": "2026-01-01",
        "bindings": [
            {"type": "secret_text", "name": "DISCORD_PUBLIC_KEY",
             "text": need("DISCORD_PUBLIC_KEY")},
            {"type": "secret_text", "name": "GITHUB_TOKEN",
             "text": need("GH_DISPATCH_TOKEN")},
            {"type": "secret_text", "name": "GITHUB_REPO",
             "text": need("GH_REPO")},
        ],
    }
    files = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "worker.js": ("worker.js", code, "application/javascript+module"),
    }
    r = requests.put(f"{API}/accounts/{aid}/workers/scripts/{SCRIPT}",
                     headers=h, files=files, timeout=60)
    body = r.json()
    if not body.get("success"):
        raise SystemExit(f"upload failed: {json.dumps(body)[:600]}")
    print(f"✅ uploaded worker '{SCRIPT}' with 3 secrets")

    # Give it a public https://<script>.<subdomain>.workers.dev address.
    r = requests.post(f"{API}/accounts/{aid}/workers/scripts/{SCRIPT}/subdomain",
                      headers=h, json={"enabled": True}, timeout=30)
    if not r.json().get("success"):
        print(f"⚠  could not enable workers.dev URL: {r.text[:300]}")

    sub = requests.get(f"{API}/accounts/{aid}/workers/subdomain",
                       headers=h, timeout=30).json()
    name = (sub.get("result") or {}).get("subdomain")
    if name:
        url = f"https://{SCRIPT}.{name}.workers.dev"
        print(f"\n🔗 Worker URL: {url}")
        print("   Put this in Discord → your app → General Information →")
        print("   Interactions Endpoint URL")
    else:
        print("⚠  no workers.dev subdomain on this account yet — "
              "open the Workers section of the dashboard once to claim one")


if __name__ == "__main__":
    main()
