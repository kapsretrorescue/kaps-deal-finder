"""Discord: send alerts via webhook, and (optionally) READ commands.

Sending needs DISCORD_WEBHOOK_URL — that's it.

Reading `!settings` commands additionally needs DISCORD_BOT_TOKEN (a bot
application invited to your server with "Read Message History"). Without
that token everything still works; the bot just can't hear commands.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("dealfinder.discord")

DISCORD_CHAR_LIMIT = 2000
API = "https://discord.com/api/v10"


def _chunk(text: str, limit: int = DISCORD_CHAR_LIMIT - 100) -> list[str]:
    """Split on blank lines so listings don't get cut mid-entry."""
    chunks, current = [], ""
    for block in text.split("\n\n"):
        # A single oversized block (rare) gets hard-split so nothing is lost
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
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


def _channel_id() -> str | None:
    """The webhook itself tells us which channel it posts to."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json().get("channel_id")
    except (requests.RequestException, ValueError) as e:
        log.warning("Could not resolve webhook channel: %s", e)
        return None


def read_commands(after_id: str | None) -> tuple[list[str], str | None]:
    """Fetch messages posted after `after_id`.

    Returns (contents oldest-first, newest message id seen). Bot's own
    messages are skipped so it can never answer itself.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return [], after_id
    channel = _channel_id()
    if not channel:
        return [], after_id

    params = {"limit": "50"}
    if after_id:
        params["after"] = after_id
    try:
        resp = requests.get(
            f"{API}/channels/{channel}/messages",
            headers={"Authorization": f"Bot {token}"},
            params=params, timeout=30,
        )
        resp.raise_for_status()
        messages = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Could not read Discord messages: %s", e)
        return [], after_id

    if not messages:
        return [], after_id
    newest = messages[0]["id"]        # Discord returns newest-first
    contents = [m.get("content", "") for m in reversed(messages)
                if not m.get("webhook_id") and not m.get("author", {}).get("bot")]
    return contents, newest
