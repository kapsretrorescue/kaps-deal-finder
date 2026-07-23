"""Reddit r/hardwareswap via the official Reddit API.

Setup (one-time, ~5 min):
  1. Go to https://www.reddit.com/prefs/apps while logged in
  2. "create another app" -> type: script -> redirect uri can be
     http://localhost (unused)
  3. Put the id (under the app name) and secret in .env as
     REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET

Notes on how this source behaves:
  - Swap posts look like "[USA-CA] [H] Game Boy Color [W] PayPal".
    [H] = what they Have, [W] = what they Want. We only match consoles
    against the part of the title BEFORE [W], so someone *looking for*
    a Game Boy doesn't trigger an alert.
  - Prices live in free text. If we find exactly one $-amount in the post
    we use it; if we find several (multi-item posts), the listing is still
    surfaced in your digest under "no clean price — open and check".
"""
from __future__ import annotations

import logging
import os
import re

import requests

from .base import Listing

log = logging.getLogger("dealfinder.reddit")

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)")


def fetch(consoles: dict, settings: dict) -> list[Listing]:
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "windows:kaps-deal-finder:v1.0")
    if not client_id or not client_secret:
        log.info("Reddit skipped: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set in .env")
        return []

    # App-only token: read-only access, no reddit account password involved.
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": user_agent},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}

    cfg = settings["sources"]["reddit"]
    listings: list[Listing] = []

    for sub in cfg.get("subreddits", ["hardwareswap"]):
        try:
            resp = requests.get(
                f"https://oauth.reddit.com/r/{sub}/new",
                headers=headers,
                params={"limit": str(cfg.get("max_posts", 75))},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("Reddit fetch failed for r/%s: %s", sub, e)
            continue

        for child in resp.json().get("data", {}).get("children", []):
            post = child.get("data", {})
            title = post.get("title", "")
            body = post.get("selftext", "")

            # Only the [H] side of the title counts as "console for sale".
            have_side = title.lower().split("[w]")[0]
            if not any(
                kw in have_side
                for c in consoles.values()
                for kw in c["keywords"]
            ):
                continue

            prices = PRICE_RE.findall(f"{title} {body}")
            price = float(prices[0]) if len(prices) == 1 else None
            note = "" if price else "no clean price parsed — open and check"

            listings.append(Listing(
                source="reddit",
                listing_id=post.get("name", post.get("id", "")),
                title=title,
                url="https://www.reddit.com" + post.get("permalink", ""),
                description=body[:2000],
                price=price,
                price_note=note,
            ))

    log.info("Reddit: %d candidate posts fetched", len(listings))
    return listings
