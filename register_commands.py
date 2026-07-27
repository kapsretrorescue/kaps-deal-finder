"""Register the Discord slash commands for Kap's Retro Rescue.

Registers to the server directly (not globally) so changes appear instantly
instead of taking up to an hour to propagate.

    .venv\\Scripts\\python.exe register_commands.py            # show them
    .venv\\Scripts\\python.exe register_commands.py --apply    # register
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
API = "https://discord.com/api/v10"
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
APP_ID = "1530856962219184260"
H = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

STRING, NUMBER = 3, 10

COMMANDS = [
    {"name": "scan", "description": "Scan every console for deals right now"},
    {"name": "search", "description": "Search one console for deals",
     "options": [{"name": "console", "description": "e.g. dslite, gba-sp, 3ds",
                  "type": STRING, "required": True}]},
    {"name": "settings", "description": "Show current pricing and alert settings"},
    {"name": "consoles", "description": "List tracked consoles and max buy prices"},
    {"name": "stats", "description": "How accurate have the bot's estimates been?"},
    {"name": "help", "description": "Show every command"},
    {"name": "set", "description": "Change a setting",
     "options": [
         {"name": "path", "description": "e.g. pricing.refurb_cost",
          "type": STRING, "required": True},
         {"name": "value", "description": "the new value",
          "type": STRING, "required": True}]},
    {"name": "list", "description": "Post a refurbished console to #for-sale",
     "options": [
         {"name": "console", "description": "e.g. gba-sp", "type": STRING,
          "required": True},
         {"name": "price", "description": "asking price in dollars",
          "type": NUMBER, "required": True},
         {"name": "notes", "description": "condition, mods, what's included",
          "type": STRING, "required": False}]},
]


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN missing from .env")
    apply = "--apply" in sys.argv

    guilds = requests.get(f"{API}/users/@me/guilds", headers=H, timeout=30).json()
    if not guilds:
        raise SystemExit("bot isn't in a server")
    gid = guilds[0]["id"]
    print(f"Server: {guilds[0]['name']}\n")

    for c in COMMANDS:
        opts = "".join(f" {o['name']}:" for o in c.get("options", []))
        print(f"  /{c['name']}{opts}  — {c['description']}")

    if not apply:
        print("\nDry run — re-run with --apply to register them.")
        return

    r = requests.put(f"{API}/applications/{APP_ID}/guilds/{gid}/commands",
                     headers=H, json=COMMANDS, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"registration failed: {r.status_code} {r.text[:300]}")
    print(f"\n✅ registered {len(r.json())} slash commands")
    print("They appear in Discord as soon as the interactions endpoint is set.")


if __name__ == "__main__":
    main()
