"""Build out the Kap's Retro Rescue Discord server.

Creates a public customer-facing side and a locked staff area, then moves
the existing deal-alert channel into the staff area so customers never see
your buying prices or margins.

    .venv\\Scripts\\python.exe setup_server.py            # show the plan
    .venv\\Scripts\\python.exe setup_server.py --apply    # build it

Safe to re-run: anything that already exists is left alone.
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
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
BOT_ID = "1530856962219184260"
H = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

VIEW = 1 << 10          # 1024      View Channel
SEND = 1 << 11          # 2048      Send Messages
HISTORY = 1 << 16       # 65536     Read Message History
THREADS = 1 << 35       #           Create Public Threads
REACT = 1 << 6          # 64        Add Reactions

# name, topic, customers_can_post
PUBLIC = {
    "📋 information": [
        ("welcome", "What we do, how it works, and how to reach us.", False),
        ("faq-and-pricing", "Turnaround times, pricing, and what we can fix.", False),
    ],
    "🎮 shop": [
        ("for-sale", "Refurbished consoles currently available.", False),
        ("showcase", "Before and after — restorations we're proud of.", False),
    ],
    "🔧 support": [
        ("repair-requests", "Tell us what's broken and we'll quote it. "
                            "Photos help a lot!", True),
        ("general", "Chat about retro handhelds.", True),
    ],
}
STAFF_CATEGORY = "🔒 staff"
STAFF_EXTRA = [("inventory", "Units in hand, in progress, and sold.")]
ALERTS_CHANNEL = "deal-alerts"

WELCOME_TEXT = """# Kap's Retro Rescue
**Nostalgia Restored** · kapsretrorescue.com

We bring dead handhelds back to life — Game Boy, Game Boy Color, Advance,
SP, DS, DS Lite, DSi, 3DS and 2DS. Full refurbishment: new shells, fresh
buttons, retrobrighting, screen work, battery and charge-port repairs.

**Got something broken?** Post it in #repair-requests with a photo and a
short description of what's wrong.
**Looking to buy?** Refurbished units get posted in #for-sale.
**Curious what we can do?** #showcase has before-and-afters.

Questions about price or turnaround → #faq-and-pricing.
"""

FAQ_TEXT = """# FAQ & Pricing

**What do you repair?**
Nintendo handhelds: Game Boy (DMG), Game Boy Color, Game Boy Advance,
GBA SP, DS, DS Lite, DSi, 3DS, 2DS and the New 3DS/2DS XL.

**Common jobs**
· Shell replacement & retrobrighting (yellowed plastic)
· Screen replacement — cracked, lines, no display
· Button / membrane replacement (sticky or unresponsive)
· Charge port repair and battery replacement
· Battery-acid corrosion cleanup
· Full deep clean and refurbishment

**How much?**
Depends on the console and what's wrong — post in #repair-requests with
photos for a quote. *(Owner: replace this with your real price list.)*

**How long does it take?**
*(Owner: add your typical turnaround here.)*

**Do you buy broken consoles?**
Yes — dead, untested, or in bulk. Send details in #repair-requests.
"""


def api(method: str, path: str, **kw):
    r = requests.request(method, API + path, headers=H, timeout=30, **kw)
    if r.status_code == 429:                      # rate limited: wait it out
        time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
        r = requests.request(method, API + path, headers=H, timeout=30, **kw)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path} failed: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else {}


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN missing from .env")
    apply = "--apply" in sys.argv

    guilds = api("GET", "/users/@me/guilds")
    if not guilds:
        raise SystemExit("Bot isn't in any server.")
    guild = guilds[0]
    gid = guild["id"]
    print(f"Server: {guild['name']} ({gid})\n")

    channels = api("GET", f"/guilds/{gid}/channels")
    existing = {c["name"]: c for c in channels}
    roles = api("GET", f"/guilds/{gid}/roles")
    everyone = next(r["id"] for r in roles if r["name"] == "@everyone")

    # --- Staff role --------------------------------------------------------
    staff = next((r for r in roles if r["name"] == "Staff"), None)
    if staff:
        print("· Staff role already exists")
    elif apply:
        staff = api("POST", f"/guilds/{gid}/roles", json={
            "name": "Staff", "color": 0x2ED573, "hoist": True,
            "permissions": "0", "mentionable": False})
        print("✅ created Staff role")
        owner_id = api("GET", f"/guilds/{gid}")["owner_id"]
        try:
            api("PUT", f"/guilds/{gid}/members/{owner_id}/roles/{staff['id']}")
            print("✅ gave you the Staff role")
        except SystemExit as e:
            print(f"⚠ couldn't assign Staff role: {e}")
    else:
        print("· would create Staff role (and give it to you)")

    staff_id = staff["id"] if staff else None

    def category(name: str, private: bool) -> str | None:
        """Create a category if missing; return its id."""
        if name in existing:
            print(f"· category {name} exists")
            return existing[name]["id"]
        if not apply:
            print(f"· would create category {name}{' (private)' if private else ''}")
            return None
        overwrites = []
        if private:
            overwrites = [
                {"id": everyone, "type": 0, "deny": str(VIEW)},
                {"id": BOT_ID, "type": 1, "allow": str(VIEW | SEND | HISTORY)},
            ]
            if staff_id:
                overwrites.append({"id": staff_id, "type": 0,
                                   "allow": str(VIEW | SEND | HISTORY)})
        cat = api("POST", f"/guilds/{gid}/channels",
                  json={"name": name, "type": 4,
                        "permission_overwrites": overwrites})
        print(f"✅ created category {name}")
        existing[name] = cat
        return cat["id"]

    # --- Staff side FIRST --------------------------------------------------
    # The existing #general is where deal alerts land today. Move and rename
    # it before building the public side, otherwise the public #general would
    # collide with it — and a fresh public #general gets created below.
    staff_parent = category(STAFF_CATEGORY, private=True)
    alerts = existing.get(ALERTS_CHANNEL) or existing.get("general")
    if alerts and alerts["name"] == ALERTS_CHANNEL and \
            alerts.get("parent_id") == staff_parent:
        print(f"· #{ALERTS_CHANNEL} already in the staff area")
    elif alerts and apply:
        api("PATCH", f"/channels/{alerts['id']}", json={
            "name": ALERTS_CHANNEL, "parent_id": staff_parent,
            "topic": "Deal alerts from the bot. Commands work here: "
                     "!scan, !search <console>, !settings, !help"})
        print(f"✅ moved #{alerts['name']} → #{ALERTS_CHANNEL} (staff only)")
        existing.pop("general", None)          # freed up for the public side
        alerts["name"] = ALERTS_CHANNEL
        existing[ALERTS_CHANNEL] = alerts
    elif alerts:
        print(f"· would move #{alerts['name']} → #{ALERTS_CHANNEL} (staff only)")
        if alerts["name"] == "general":
            existing.pop("general", None)      # so the dry run reads true

    for cname, topic in STAFF_EXTRA:
        if cname in existing:
            print(f"  · #{cname} exists")
        elif apply:
            api("POST", f"/guilds/{gid}/channels", json={
                "name": cname, "type": 0, "parent_id": staff_parent,
                "topic": topic})
            print(f"  ✅ #{cname}")
        else:
            print(f"  · would create #{cname}")

    # --- Public side -------------------------------------------------------
    for cat_name, chans in PUBLIC.items():
        parent = category(cat_name, private=False)
        for cname, topic, can_post in chans:
            if cname in existing:
                print(f"  · #{cname} exists")
                continue
            if not apply:
                print(f"  · would create #{cname}"
                      f"{'' if can_post else ' (read-only)'}")
                continue
            ow = []
            if not can_post:
                # Announcement-style: customers read, staff posts
                ow = [{"id": everyone, "type": 0, "deny": str(SEND),
                       "allow": str(REACT)}]
                if staff_id:
                    ow.append({"id": staff_id, "type": 0, "allow": str(SEND)})
            ch = api("POST", f"/guilds/{gid}/channels", json={
                "name": cname, "type": 0, "parent_id": parent,
                "topic": topic, "permission_overwrites": ow})
            existing[cname] = ch
            print(f"  ✅ #{cname}")

    # --- Starter content ---------------------------------------------------
    if apply:
        for cname, text in (("welcome", WELCOME_TEXT), ("faq-and-pricing", FAQ_TEXT)):
            ch = existing.get(cname)
            if not ch:
                continue
            msgs = api("GET", f"/channels/{ch['id']}/messages?limit=5")
            if msgs:
                print(f"· #{cname} already has messages, leaving it alone")
                continue
            api("POST", f"/channels/{ch['id']}/messages", json={"content": text})
            print(f"✅ posted starter content in #{cname}")

    print("\nDone." if apply else "\nDry run — re-run with --apply to build it.")


if __name__ == "__main__":
    main()
