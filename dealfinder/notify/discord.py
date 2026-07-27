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


EMBEDS_PER_MESSAGE = 10       # Discord's hard limit


def send(digest_text: str = "", embeds: list | None = None) -> bool:
    """Post to the channel. With `embeds` you get compact colour-coded cards
    (10 per message max); plain text is chunked to Discord's 2000-char limit."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        log.warning("Discord notify enabled but DISCORD_WEBHOOK_URL not set in .env")
        return False

    def post(payload: dict) -> bool:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code >= 300:
            log.error("Discord webhook failed: %s %s",
                      resp.status_code, resp.text[:300])
            return False
        return True

    if embeds:
        ok = True
        for i in range(0, len(embeds), EMBEDS_PER_MESSAGE):
            batch = embeds[i:i + EMBEDS_PER_MESSAGE]
            payload = {"embeds": batch}
            if i == 0 and digest_text:
                payload["content"] = digest_text[:1900]
            ok = post(payload) and ok
        return ok

    if not digest_text.strip():
        return True
    return all(post({"content": chunk}) for chunk in _chunk(digest_text))


def post_to_channel(channel_name: str, content: str = "",
                    embeds: list | None = None) -> bool:
    """Post to a channel by name (needs the bot token, not the webhook).

    The webhook can only ever post to its own channel, so anything aimed at
    #for-sale or another channel goes through here.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        log.warning("post_to_channel needs DISCORD_BOT_TOKEN")
        return False
    h = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        guilds = requests.get(f"{API}/users/@me/guilds", headers=h, timeout=30).json()
        if not guilds:
            return False
        chans = requests.get(f"{API}/guilds/{guilds[0]['id']}/channels",
                             headers=h, timeout=30).json()
        target = next((c for c in chans if c["name"] == channel_name), None)
        if not target:
            log.warning("No channel named %s", channel_name)
            return False
        payload: dict = {}
        if content:
            payload["content"] = content[:1900]
        if embeds:
            payload["embeds"] = embeds[:10]
        r = requests.post(f"{API}/channels/{target['id']}/messages",
                          headers=h, json=payload, timeout=30)
        if r.status_code >= 300:
            log.error("post to #%s failed: %s %s", channel_name,
                      r.status_code, r.text[:200])
            return False
        return True
    except (requests.RequestException, ValueError, KeyError) as e:
        log.error("post_to_channel failed: %s", e)
        return False


def grant_role(user_id: str, role_name: str, remove: bool = False) -> tuple[bool, str]:
    """Add or remove a server role by name. Returns (ok, message)."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return False, "DISCORD_BOT_TOKEN not set"
    h = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        gid = requests.get(f"{API}/users/@me/guilds", headers=h, timeout=30).json()[0]["id"]
        roles = requests.get(f"{API}/guilds/{gid}/roles", headers=h, timeout=30).json()
        role = next((r for r in roles if r["name"].lower() == role_name.lower()), None)
        if not role:
            return False, f"no role called {role_name}"
        url = f"{API}/guilds/{gid}/members/{user_id}/roles/{role['id']}"
        r = requests.delete(url, headers=h, timeout=30) if remove else \
            requests.put(url, headers=h, timeout=30)
        if r.status_code >= 300:
            return False, f"Discord said {r.status_code}: {r.text[:120]}"
        return True, "done"
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        return False, str(e)


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
