"""Upgrade Kap's Retro Rescue from 'channels exist' to a real business server.

What this does:
  · sets the server icon from your logo
  · turns on Discord Community mode (rules screening, member verification)
  · rebuilds #repair-requests as a FORUM — every request becomes its own
    tracked post with console + status tags, so nothing gets lost in a feed
  · rebuilds #showcase as a MEDIA channel — a proper portfolio grid
  · adds #announcements (followable by other servers)
  · turns on AutoMod (spam, slurs, mass-mention protection) instead of
    inviting a third-party moderation bot
  · tidies leftover default channels, orders categories, adds slow mode
  · prints a permanent invite link

    .venv\\Scripts\\python.exe setup_server_pro.py            # show the plan
    .venv\\Scripts\\python.exe setup_server_pro.py --apply    # do it
"""
from __future__ import annotations

import base64
import mimetypes
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
LOGO = Path(r"C:\Users\pbowe\Downloads\image.jpg")

TEXT, ANNOUNCEMENT, CATEGORY, FORUM, MEDIA = 0, 5, 4, 15, 16

INTAKE_GUIDELINES = """Post one console per request and we'll quote it.

Include:
• Console (e.g. DS Lite, Game Boy Advance SP)
• What's wrong — screen, buttons, won't charge, won't power on…
• What you want — repair / shell swap / IPS upgrade / not sure
• Photos — front, back, and the problem area
• Your city & state, for shipping

Photos make quotes faster and far more accurate. Not sure what you need?
Describe what it's doing and we'll work it out.

Tag your post with your console. We'll set the status tags as your repair
moves along, so you can always see where it's at."""

SHOWCASE_TOPIC = """Before & after from the bench — yellowed shells brought back, cracked \
screens replaced, dead consoles running again. Want yours to look like this? \
Head to #repair-requests."""

FORUM_TAGS = [
    # customers pick these
    {"name": "Game Boy", "moderated": False},
    {"name": "Game Boy Color", "moderated": False},
    {"name": "GBA / SP", "moderated": False},
    {"name": "DS / DS Lite", "moderated": False},
    {"name": "DSi", "moderated": False},
    {"name": "3DS / 2DS", "moderated": False},
    {"name": "Sourced Build", "moderated": False},
    # staff-only status tags (moderated = customers can't set these)
    {"name": "💬 Quote Sent", "moderated": True},
    {"name": "📦 Awaiting Console", "moderated": True},
    {"name": "🔧 In Progress", "moderated": True},
    {"name": "✅ Completed", "moderated": True},
]


def api(method: str, path: str, **kw):
    r = requests.request(method, API + path, headers=H, timeout=60, **kw)
    if r.status_code == 429:
        time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
        r = requests.request(method, API + path, headers=H, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:400]}")
    return r.json() if r.text else {}


def try_api(label: str, method: str, path: str, **kw):
    """Run a call, but keep going if Discord refuses — one unsupported
    feature shouldn't abandon the rest of the setup."""
    try:
        result = api(method, path, **kw)
        print(f"✅ {label}")
        return result
    except RuntimeError as e:
        print(f"⚠  {label} — skipped: {str(e)[:160]}")
        return None


def main() -> None:
    apply = "--apply" in sys.argv
    guild = api("GET", "/users/@me/guilds")[0]
    gid = guild["id"]
    g = api("GET", f"/guilds/{gid}")
    chans = api("GET", f"/guilds/{gid}/channels")
    by_name = {c["name"]: c for c in chans}
    cats = {c["name"]: c for c in chans if c["type"] == CATEGORY}
    features = set(g.get("features", []))
    print(f"Server: {g['name']}\n")

    if not apply:
        print("· set server icon from your logo")
        print("· enable Community mode (rules screening + verified members)")
        print("· #repair-requests → FORUM with console & status tags")
        print("· #showcase → MEDIA channel (portfolio grid)")
        print("· add #announcements")
        print("· enable AutoMod: spam, slurs, mass-mention")
        print("· delete leftover empty 'Text Channels' category")
        print("· rename voice 'General' → 'Bench Chat'")
        print("· 30s slow mode on public #general")
        print("· order categories: information → shop → support → staff")
        print("· create a permanent invite link")
        print("\nDry run — re-run with --apply.")
        return

    # ---- 1. Server icon ---------------------------------------------------
    if LOGO.is_file():
        mime = mimetypes.guess_type(LOGO.name)[0] or "image/png"
        uri = f"data:{mime};base64," + base64.b64encode(LOGO.read_bytes()).decode()
        try_api("server icon set", "PATCH", f"/guilds/{gid}", json={"icon": uri})
    else:
        print(f"⚠  logo not found at {LOGO} — skipping server icon")

    # ---- 2. Community mode ------------------------------------------------
    # Required before forum/media channels can exist at all.
    if "COMMUNITY" not in features:
        staff_cat = cats.get("🔒 staff")
        updates = by_name.get("discord-updates")
        if not updates and staff_cat:
            updates = try_api("created #discord-updates (staff)", "POST",
                              f"/guilds/{gid}/channels",
                              json={"name": "discord-updates", "type": TEXT,
                                    "parent_id": staff_cat["id"],
                                    "topic": "Discord posts community notices here."})
        rules = by_name.get("welcome")
        if rules and updates:
            ok = try_api("Community mode enabled", "PATCH", f"/guilds/{gid}", json={
                "features": sorted(features | {"COMMUNITY"}),
                "rules_channel_id": rules["id"],
                "public_updates_channel_id": updates["id"],
                "verification_level": 1,          # must have a verified email
                "explicit_content_filter": 2,     # scan all members' media
                "default_message_notifications": 1,
            })
            if ok:
                features.add("COMMUNITY")
                time.sleep(2)
    else:
        print("· Community mode already on")

    # ---- 3. Forum + media channels ---------------------------------------
    support = cats.get("🔧 support")
    shop = cats.get("🎮 shop")

    if "COMMUNITY" in features:
        old = by_name.get("repair-requests")
        if old and old["type"] != FORUM:
            new = try_api("#repair-requests rebuilt as a forum", "POST",
                          f"/guilds/{gid}/channels",
                          json={"name": "repair-requests", "type": FORUM,
                                "parent_id": support["id"] if support else None,
                                "topic": INTAKE_GUIDELINES,
                                "available_tags": FORUM_TAGS,
                                "default_auto_archive_duration": 10080})
            if new:
                try_api("removed the old text channel", "DELETE",
                        f"/channels/{old['id']}")

        old = by_name.get("showcase")
        if old and old["type"] != MEDIA:
            new = try_api("#showcase rebuilt as a media gallery", "POST",
                          f"/guilds/{gid}/channels",
                          json={"name": "showcase", "type": MEDIA,
                                "parent_id": shop["id"] if shop else None,
                                "topic": SHOWCASE_TOPIC,
                                "default_auto_archive_duration": 10080})
            if new:
                try_api("removed the old text channel", "DELETE",
                        f"/channels/{old['id']}")
    else:
        print("⚠  forum/media channels need Community mode — skipped")

    # ---- 4. Announcements -------------------------------------------------
    info = cats.get("📋 information")
    if "announcements" not in by_name and info:
        try_api("#announcements created", "POST", f"/guilds/{gid}/channels",
                json={"name": "announcements",
                      "type": ANNOUNCEMENT if "COMMUNITY" in features else TEXT,
                      "parent_id": info["id"],
                      "topic": "New builds, restock, and shop news."})

    # ---- 5. AutoMod (instead of a third-party moderation bot) -------------
    existing_rules = {r["name"] for r in
                      (try_api("read AutoMod rules", "GET",
                               f"/guilds/{gid}/auto-moderation/rules") or [])}
    if "Block spam" not in existing_rules:
        try_api("AutoMod: spam blocking", "POST",
                f"/guilds/{gid}/auto-moderation/rules",
                json={"name": "Block spam", "event_type": 1, "trigger_type": 3,
                      "actions": [{"type": 1, "metadata": {
                          "custom_message": "That looked like spam."}}],
                      "enabled": True})
    if "Block harmful words" not in existing_rules:
        try_api("AutoMod: slurs & severe profanity", "POST",
                f"/guilds/{gid}/auto-moderation/rules",
                json={"name": "Block harmful words", "event_type": 1,
                      "trigger_type": 4,
                      "trigger_metadata": {"presets": [2, 3]},
                      "actions": [{"type": 1, "metadata": {
                          "custom_message": "Let's keep it friendly."}}],
                      "enabled": True})
    if "Block mass mentions" not in existing_rules:
        try_api("AutoMod: mass-mention protection", "POST",
                f"/guilds/{gid}/auto-moderation/rules",
                json={"name": "Block mass mentions", "event_type": 1,
                      "trigger_type": 5,
                      "trigger_metadata": {"mention_total_limit": 6},
                      "actions": [{"type": 1, "metadata": {
                          "custom_message": "Too many mentions."}}],
                      "enabled": True})

    # ---- 6. Tidy up defaults ---------------------------------------------
    chans = api("GET", f"/guilds/{gid}/channels")
    by_name = {c["name"]: c for c in chans}
    for c in chans:
        if c["type"] == CATEGORY and c["name"] in ("Text Channels", "Voice Channels"):
            kids = [x for x in chans if x.get("parent_id") == c["id"]]
            if not kids:
                try_api(f"deleted empty category '{c['name']}'", "DELETE",
                        f"/channels/{c['id']}")
    voice = next((c for c in chans if c["type"] == 2 and c["name"] == "General"), None)
    if voice:
        try_api("voice 'General' → 'Bench Chat'", "PATCH",
                f"/channels/{voice['id']}", json={"name": "Bench Chat"})

    pub_general = next((c for c in chans if c["name"] == "general"
                        and c["type"] == TEXT), None)
    if pub_general:
        try_api("30s slow mode on #general", "PATCH",
                f"/channels/{pub_general['id']}", json={"rate_limit_per_user": 30})

    # ---- 7. Category order ------------------------------------------------
    order = ["📋 information", "🎮 shop", "🔧 support", "🔒 staff"]
    payload = [{"id": cats[n]["id"], "position": i}
               for i, n in enumerate(order) if n in cats]
    if payload:
        try_api("categories reordered", "PATCH", f"/guilds/{gid}/channels",
                json=payload)

    # ---- 8. Permanent invite ---------------------------------------------
    target = by_name.get("welcome")
    if target:
        inv = try_api("permanent invite created", "POST",
                      f"/channels/{target['id']}/invites",
                      json={"max_age": 0, "max_uses": 0, "unique": False})
        if inv:
            print(f"\n🔗 Share this: https://discord.gg/{inv['code']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
