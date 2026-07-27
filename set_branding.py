"""Apply the Kap's Retro Rescue logo to the Discord bot and deal alerts.

Usage:
    .venv\\Scripts\\python.exe set_branding.py "C:\\path\\to\\logo.png"

Sets:
  - the webhook avatar  -> the picture on every deal card
  - the bot application icon -> the bot's profile picture

Discord wants PNG/JPG/GIF under ~8 MB, square looks best.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in ("image/png", "image/jpeg", "image/gif"):
        raise SystemExit(f"Discord won't accept {mime} — use PNG, JPG or GIF.")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1]).expanduser()
    if not path.is_file():
        raise SystemExit(f"No such file: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise SystemExit("Image is over 8 MB — Discord will reject it.")

    uri = data_uri(path)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if webhook:
        r = requests.patch(webhook, json={"avatar": uri}, timeout=60)
        print("deal-alert avatar:", "✅ set" if r.ok else f"❌ {r.text[:160]}")

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        r = requests.patch(
            "https://discord.com/api/v10/applications/@me",
            headers={"Authorization": f"Bot {token}"},
            json={"icon": uri}, timeout=60)
        print("bot profile picture:", "✅ set" if r.ok else f"❌ {r.text[:160]}")


if __name__ == "__main__":
    main()
