"""Discord webhook notifier.

Setup (~30 seconds):
  Server Settings -> Integrations -> Webhooks -> New Webhook -> pick a
  channel -> Copy Webhook URL -> paste into .env as DISCORD_WEBHOOK_URL.
Install the Discord app on your phone and enable notifications for that
channel to get push alerts like a text message.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("dealfinder.discord")

DISCORD_CHAR_LIMIT = 2000  # per-message hard limit


def _chunk(text: str, limit: int = DISCORD_CHAR_LIMIT - 100) -> list[str]:
    """Split on blank lines so listings don't get cut mid-entry."""
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > limit and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


def send(digest_text: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        log.warning("Discord notify enabled but DISCORD_WEBHOOK_URL not set in .env")
        return False
    ok = True
    for chunk in _chunk(digest_text):
        resp = requests.post(url, json={"content": chunk}, timeout=30)
        if resp.status_code >= 300:
            log.error("Discord webhook failed: %s %s", resp.status_code, resp.text[:200])
            ok = False
    return ok
