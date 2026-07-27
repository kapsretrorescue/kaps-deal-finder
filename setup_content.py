"""Fill the public Discord channels with real Kap's Retro Rescue content.

Pricing and policies come from the Phase 1 Playbook and Master Plan
(July 2026). Re-running replaces the bot's previous posts in those
channels, so edit the constants here and run again to update.

    .venv\\Scripts\\python.exe setup_content.py --apply
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
GREEN = 0x2ED573
NAVY = 0x1B2A4A

WELCOME = """# 🎮 Kap's Retro Rescue
**Nostalgia Restored** · kapsretrorescue.com

We bring dead handhelds back to life — repairs, restorations, upgrades, and
fully refurbished consoles ready to play.

**Consoles we service**
Game Boy · Game Boy Color · Game Boy Advance · Game Boy Advance SP
Nintendo DS · DS Lite · DSi · 3DS · 2DS · New 3DS / New 2DS XL

**What we do**
🔧 **Repairs** — screens, charge ports, batteries, buttons
✨ **Restoration** — retrobright, deep clean, full shell swaps
⚡ **Upgrades** — IPS backlit screens, USB-C charging, homebrew
🕹️ **Sourced builds** — refurbished consoles, ready to play

**Where to go**
→ <#{repair}> — tell us what's broken and get a quote
→ <#{faq}> — full price list and common questions
→ <#{sale}> — refurbished consoles available now
→ <#{show}> — before & after from the bench

**How mail-in works**
1️⃣ Post in <#{repair}> or order at kapsretrorescue.com
2️⃣ We send packing instructions
3️⃣ Your console arrives — we photograph it and confirm the issue
4️⃣ We do the work, then test *everything*, not just the part we touched
5️⃣ Ships back with tracking — return shipping is already in your price

*Every console is photographed on arrival and before it ships back. We never
do work you haven't approved, and we never ship a console we haven't tested.*
"""

PRICE_EMBEDS = [
    {
        "title": "🐚 Shell Swaps & Restoration",
        "color": GREEN,
        "description":
            "*Every shell swap includes matching accessories at no extra charge.*\n\n"
            "**Shell swap** — includes screen protector + case\n"
            "Game Boy (DMG) · Game Boy Color — **$55**\n"
            "Game Boy Advance · GBA SP — **$58**\n\n"
            "**Shell swap** — includes protector, case **+ matching stylus**\n"
            "DS · DS Lite — **$65**\n"
            "3DS · 2DS · New 3DS family — **$85**\n\n"
            "**Custom colour / clear shell build** — $58–$95 (console dependent)\n"
            "**Retrobright / shell restoration** — **$30**\n"
            "**Deep clean & restoration** — **$25**",
        "footer": {"text": "Retrobright is not offered on the 3DS/2DS family — "
                           "see the FAQ for why. Those get a deep clean instead."},
    },
    {
        "title": "🔧 Repairs & Upgrades",
        "color": GREEN,
        "description":
            "**Screen replacement (OEM)**\n"
            "Game Boy Color $35 · Game Boy (DMG) $40 · GBA $40 · GBA SP $40\n"
            "DS / DS Lite $70 per screen · DSi $70 · 3DS / 2DS $80\n"
            "New 3DS / New 2DS XL $90\n\n"
            "**IPS backlit screen upgrade**\n"
            "Game Boy Color **$100** · Game Boy Advance **$105**\n"
            "GBA SP **$120** · DS Lite **$130** per screen\n\n"
            "**USB-C charging mod**\n"
            "GBA SP $55 · DSi $55 · DS / DS Lite $60 · 3DS family $65\n\n"
            "**Battery replacement** (DS line) — **$35**\n"
            "**Charge port resolder** (DS Lite / DSi) — **$45**\n"
            "**CFW / homebrew install** (3DS / 2DS family) — **$50**\n"
            "**High-res screen upgrade** (New 3DS / New 2DS XL) — quote only",
    },
    {
        "title": "📦 Bundles — mail-in only",
        "color": GREEN,
        "description":
            "Cheaper than booking the same services separately.\n\n"
            "**Fresh Start** — retrobright + deep clean\n"
            "**$45** *(3DS family: deep clean only — $22)*\n\n"
            "**Full Revival** ⭐ *most popular*\n"
            "Shell swap (accessories included) + retrobright + deep clean\n"
            "**$94–$102** *(DSi $85)*\n\n"
            "**Complete Overhaul**\n"
            "Full Revival + IPS screen / USB-C where available\n"
            "**$180–$265**",
    },
    {
        "title": "🕹️ Sourced Builds — we find it, fix it, ship it",
        "color": NAVY,
        "description":
            "Don't have a console to send in? We source one, fully refurbish "
            "it, and ship it ready to play.\n\n"
            "Game Boy (DMG) — **$100**\n"
            "Game Boy Color — **$115**\n"
            "Game Boy Advance — **$135**\n"
            "Game Boy Advance SP — **$160**\n"
            "DS / DS Lite — **$115**\n"
            "DSi — **$130**\n"
            "3DS / 2DS — **$175**\n"
            "New 3DS / New 2DS XL — **$225**\n\n"
            "Mods can be added on top at **25% off** standard pricing.",
        "footer": {"text": "Lead time 7–14 business days (donor availability "
                           "varies) · customer pays outbound shipping · "
                           "bundles don't apply to sourced builds"},
    },
    {
        "title": "🎒 Accessories",
        "color": NAVY,
        "description":
            "Screen protector **$8** · Stylus **$5** · Stylus 3-pack **$12**\n"
            "Silicone case **$13** · Clear / crystal case **$15**\n"
            "Charger **$15** · Battery (part only) **$18**\n\n"
            "**Accessory bundle** — case + protector + stylus — **$28**",
        "footer": {"text": "Accessory-only orders: customer pays shipping. "
                           "Ordered alongside a repair? Ships in the same box, free."},
    },
]

FAQ = """## ❓ Common questions

**Do your prices include shipping?**
Yes — every service price covers parts, labour, and return shipping back to
you. No surprise postage at the end.

**Which consoles do you work on?**
The Game Boy line (DMG, Color, Advance, SP) and the DS line (DS, DS Lite,
DSi, 3DS, 2DS, New 3DS / New 2DS XL). More systems are coming.

**Why won't you retrobright my 3DS?**
That family uses textured and soft-touch plastics that don't respond
reliably to retrobright — results are often patchy or leave a worse finish
than the original. Rather than sell you a coin flip, 3DS-family consoles get
a **deep clean & restoration** instead, which works every time.

**What if you find something worse than I described?**
We contact you before doing anything extra. You'll never be charged for work
you didn't approve.

**Is there any risk?**
Opening or modifying a console voids the manufacturer's warranty and carries
inherent risk, including data loss. We photograph every console on arrival
and test thoroughly before it ships back.

**Do you install games?**
We install homebrew and custom firmware, and can back up games **you already
own**. We don't supply or load pirated games.

**Do you buy broken consoles?**
Yes — dead, untested, or in bulk. Post details in <#{repair}>.

**Can I drop off locally instead of mailing?**
Ask — local drop-off skips return shipping, so it's cheaper.
"""

INTAKE = """## 🔧 Request a repair

Post here and we'll get you a quote. Copy this and fill it in:

```
Console:          (e.g. DS Lite, Game Boy Advance SP)
What's wrong:     (screen, buttons, won't charge, won't power on…)
What you want:    (repair / shell swap / IPS upgrade / not sure)
Photos:           (attach a couple — front, back, and the problem area)
Your location:    (city/state, for shipping)
```

📸 **Photos help enormously** — most quotes are faster and more accurate
with a clear picture of the damage.

Not sure what you need? Just describe what it's doing and we'll figure it
out. Prices are in <#{faq}>.
"""

FOR_SALE = """## 🕹️ Refurbished consoles for sale

Fully refurbished units, tested and ready to play. Everything posted here
has been cleaned, repaired, and checked end to end.

Want something specific that isn't listed? We do **sourced builds** — we
find the console, refurbish it, and ship it to you. Pricing is in <#{faq}>.
Ask in <#{repair}>.
"""

SHOWCASE = """## ✨ Before & after

Restorations from the bench — yellowed shells brought back, cracked screens
replaced, dead consoles running again.

Want yours to look like this? Start in <#{repair}>.
"""


def api(method: str, path: str, **kw):
    r = requests.request(method, API + path, headers=H, timeout=30, **kw)
    if r.status_code == 429:
        time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
        r = requests.request(method, API + path, headers=H, timeout=30, **kw)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else {}


def main() -> None:
    apply = "--apply" in sys.argv
    gid = api("GET", "/users/@me/guilds")[0]["id"]
    chans = {c["name"]: c for c in api("GET", f"/guilds/{gid}/channels")
             if c["type"] == 0}
    need = ["welcome", "faq-and-pricing", "repair-requests", "for-sale", "showcase"]
    missing = [n for n in need if n not in chans]
    if missing:
        raise SystemExit(f"Missing channels: {missing}. Run setup_server.py first.")

    ids = {"repair": chans["repair-requests"]["id"], "faq": chans["faq-and-pricing"]["id"],
           "sale": chans["for-sale"]["id"], "show": chans["showcase"]["id"]}

    plan = [
        ("welcome", WELCOME.format(**ids), None, False),
        ("faq-and-pricing", "# 💰 Pricing\n*All service prices include parts, "
                            "labour, and return shipping.*", PRICE_EMBEDS, False),
        ("faq-and-pricing", FAQ.format(**ids), None, False),
        ("repair-requests", INTAKE.format(**ids), None, True),
        ("for-sale", FOR_SALE.format(**ids), None, True),
        ("showcase", SHOWCASE.format(**ids), None, True),
    ]

    if not apply:
        for name, body, embeds, pin in plan:
            print(f"· would post to #{name}"
                  f"{f' ({len(embeds)} embeds)' if embeds else ''}"
                  f"{' + pin' if pin else ''} — {len(body)} chars")
        print("\nDry run — re-run with --apply.")
        return

    # Clear the bot's own previous posts so re-running updates cleanly
    for name in {p[0] for p in plan}:
        cid = chans[name]["id"]
        for m in api("GET", f"/channels/{cid}/messages?limit=50"):
            if m["author"].get("bot"):
                requests.delete(f"{API}/channels/{cid}/messages/{m['id']}",
                                headers=H, timeout=30)
                time.sleep(0.35)
        print(f"· cleared old posts in #{name}")

    for name, body, embeds, pin in plan:
        cid = chans[name]["id"]
        payload: dict = {"content": body}
        if embeds:
            payload["embeds"] = embeds
        msg = api("POST", f"/channels/{cid}/messages", json=payload)
        print(f"✅ posted to #{name}")
        if pin:
            api("PUT", f"/channels/{cid}/pins/{msg['id']}")
            print(f"   📌 pinned")
        time.sleep(0.5)

    print("\nDone.")


if __name__ == "__main__":
    main()
