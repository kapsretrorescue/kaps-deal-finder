"""Add #terms, the Customer role, and the owners-only area.

    .venv\\Scripts\\python.exe setup_extras.py --apply

Safe to re-run — anything that already exists is left alone.
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
BOT_ID = "1530856962219184260"

VIEW, SEND, HISTORY = 1 << 10, 1 << 11, 1 << 16
REACT, EMBED, ATTACH = 1 << 6, 1 << 14, 1 << 15

TERMS = """# 📜 Terms & Service Policy

*Please read before sending a console or placing an order.*

**Warranty**
Opening or modifying a console voids its manufacturer warranty. That's
unavoidable for any repair or mod work, and by ordering you accept it.

**Risk**
Repair and modding carry inherent risk, including data loss and, in rare
cases, a console that cannot be revived. We work carefully and test
thoroughly, but no repair can be guaranteed risk-free. Back up anything you
care about — save files, photos, homebrew data — before sending your console.

**Condition on arrival**
Every console is photographed the moment it arrives. Those photos are the
record of its condition when we received it, and we can't be responsible for
damage that was already there.

**No surprise charges**
If we find something beyond what you ordered — a cracked board, hidden
corrosion, extra damage — we contact you first. You'll never be charged for
work you didn't approve.

**If it can't be fixed**
Sometimes a console is beyond economical repair. If that happens we'll tell
you what we found, and you choose: have it returned as-is, or have us keep
it for parts. You aren't charged for repair work that couldn't be completed.

**Turnaround**
7–14 days for mail-in work. Sourced builds are 7–14 business days depending
on donor availability. You'll get an update at every stage.

**Shipping**
Return shipping is included in all service prices. For sourced builds and
accessory-only orders, outbound shipping is paid by the customer.

**Software & homebrew**
We install homebrew and custom firmware, and can back up games you already
own. We do not supply, install, or load pirated games — no exceptions.

**Payment**
Mail-in work is quoted before you send. Sourced builds are paid upfront, as
payment funds the donor console purchase.

---
*Placing an order or sending a console means you accept these terms.*
"""

LOUNGE = """# 🏆 Owners' Lounge

You're in here because you've bought from Kap's Retro Rescue — welcome.

**What this is for**
• Early word on restocks and finished builds before they go public
• Direct follow-up on anything you've bought, however long ago
• Show off your console, ask about mods, talk shop

Something wrong with a console you bought? You can always open a private
ticket — there's no time limit on that, and it doesn't expire because a
while has passed.
"""


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
    by_name = {c["name"]: c for c in chans}
    cats = {c["name"]: c for c in chans if c["type"] == 4}
    everyone = next(r["id"] for r in roles if r["name"] == "@everyone")
    staff = next((r["id"] for r in roles if r["name"] == "Staff"), None)
    customer = next((r for r in roles if r["name"] == "Customer"), None)

    if not apply:
        print("· create #terms (read-only) in 📋 information")
        print("· create the Customer role")
        print("· create #owners-lounge, visible to Customers + Staff only")
        print("\nDry run — re-run with --apply.")
        return

    # --- #terms ------------------------------------------------------------
    if "terms" in by_name:
        print("· #terms already exists")
        terms_ch = by_name["terms"]
    else:
        info = cats.get("📋 information")
        ow = [{"id": everyone, "type": 0, "deny": str(SEND), "allow": str(REACT)}]
        if staff:
            ow.append({"id": staff, "type": 0, "allow": str(SEND)})
        terms_ch = api("POST", f"/guilds/{gid}/channels", json={
            "name": "terms", "type": 0,
            "parent_id": info["id"] if info else None,
            "topic": "Warranty, risk, turnaround and payment policy. "
                     "Ordering means you accept these.",
            "permission_overwrites": ow})
        print("✅ created #terms")
    existing = api("GET", f"/channels/{terms_ch['id']}/messages?limit=10")
    for m in existing:
        if m["author"].get("bot"):
            requests.delete(f"{API}/channels/{terms_ch['id']}/messages/{m['id']}",
                            headers=H, timeout=30)
            time.sleep(0.35)
    msg = api("POST", f"/channels/{terms_ch['id']}/messages", json={"content": TERMS})
    api("PUT", f"/channels/{terms_ch['id']}/pins/{msg['id']}")
    print("✅ posted and pinned the terms")

    # --- Customer role -----------------------------------------------------
    if customer:
        print("· Customer role already exists")
    else:
        customer = api("POST", f"/guilds/{gid}/roles", json={
            "name": "Customer", "color": 0x4A90D9, "hoist": True,
            "permissions": "0", "mentionable": False})
        print("✅ created the Customer role")

    # --- Owners' lounge ----------------------------------------------------
    if "owners-lounge" in by_name:
        print("· #owners-lounge already exists")
        return
    shop = cats.get("🎮 shop")
    ow = [
        {"id": everyone, "type": 0, "deny": str(VIEW)},
        {"id": customer["id"], "type": 0,
         "allow": str(VIEW | SEND | HISTORY | EMBED | ATTACH)},
        {"id": BOT_ID, "type": 1, "allow": str(VIEW | SEND | HISTORY)},
    ]
    if staff:
        ow.append({"id": staff, "type": 0,
                   "allow": str(VIEW | SEND | HISTORY | EMBED | ATTACH)})
    ch = api("POST", f"/guilds/{gid}/channels", json={
        "name": "owners-lounge", "type": 0,
        "parent_id": shop["id"] if shop else None,
        "topic": "For people who've bought from us — restock previews, "
                 "follow-up support, and shop talk.",
        "permission_overwrites": ow})
    msg = api("POST", f"/channels/{ch['id']}/messages", json={"content": LOUNGE})
    api("PUT", f"/channels/{ch['id']}/pins/{msg['id']}")
    print("✅ created #owners-lounge (Customers + Staff only)")


if __name__ == "__main__":
    main()
