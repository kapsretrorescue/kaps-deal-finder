"""Add a private-ticket channel to the Kap's Retro Rescue server.

Uses Discord's native private threads rather than a ticket bot:

  · a customer creates a private thread in #private-support
  · only they and Staff can see it — other members can't even tell it exists
  · Staff see every ticket via the Manage Threads permission
  · works 24/7 with no bot running, so support doesn't depend on a PC
    being switched on

    .venv\\Scripts\\python.exe setup_tickets.py --apply
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
API = "https://discord.com/api/v10"
H = {"Authorization": f"Bot {os.environ.get('DISCORD_BOT_TOKEN','')}",
     "Content-Type": "application/json"}

VIEW = 1 << 10
SEND = 1 << 11
EMBED = 1 << 14
ATTACH = 1 << 15
HISTORY = 1 << 16
MANAGE_THREADS = 1 << 34
CREATE_PRIVATE_THREADS = 1 << 36
SEND_IN_THREADS = 1 << 38

CHANNEL = "private-support"
TOPIC = ("Open a private thread here and it stays between you and Kap. "
         "Use the '#+' button next to the message box, switch Private Thread "
         "on, and post your question inside. For anything after you've "
         "received your console, this is the place.")


def api(method: str, path: str, **kw):
    r = requests.request(method, API + path, headers=H, timeout=60, **kw)
    if r.status_code == 429:
        time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
        r = requests.request(method, API + path, headers=H, timeout=60, **kw)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else {}


def main() -> None:
    apply = "--apply" in sys.argv
    gid = api("GET", "/users/@me/guilds")[0]["id"]
    chans = api("GET", f"/guilds/{gid}/channels")
    roles = api("GET", f"/guilds/{gid}/roles")
    everyone = next(r["id"] for r in roles if r["name"] == "@everyone")
    staff = next((r["id"] for r in roles if r["name"] == "Staff"), None)
    support = next((c["id"] for c in chans
                    if c["type"] == 4 and c["name"] == "🔧 support"), None)

    if CHANNEL in {c["name"] for c in chans}:
        print(f"#{CHANNEL} already exists — nothing to create")
        return
    if not apply:
        print(f"· would create #{CHANNEL} in 🔧 support")
        print("  @everyone: can view + open PRIVATE threads, cannot post publicly")
        print("  Staff: can see and manage every ticket")
        print("\nDry run — re-run with --apply.")
        return

    overwrites = [
        # Customers can open a private thread and talk inside it, but can't
        # post in the channel itself — that keeps every conversation private
        # by construction rather than by asking people nicely.
        {"id": everyone, "type": 0,
         "allow": str(VIEW | HISTORY | CREATE_PRIVATE_THREADS | SEND_IN_THREADS
                      | EMBED | ATTACH),
         "deny": str(SEND)},
        {"id": os.environ.get("DISCORD_BOT_ID", "1530856962219184260"), "type": 1,
         "allow": str(VIEW | SEND | HISTORY | MANAGE_THREADS)},
    ]
    if staff:
        overwrites.append({"id": staff, "type": 0,
                           "allow": str(VIEW | SEND | HISTORY | MANAGE_THREADS
                                        | SEND_IN_THREADS)})

    ch = api("POST", f"/guilds/{gid}/channels", json={
        "name": CHANNEL, "type": 0, "parent_id": support,
        "topic": TOPIC, "permission_overwrites": overwrites})
    print(f"✅ created #{CHANNEL}")
    print("   @everyone: view + create private threads (cannot post publicly)")
    print(f"   Staff: {'full ticket access' if staff else 'NOT SET — no Staff role found'}")


if __name__ == "__main__":
    main()
