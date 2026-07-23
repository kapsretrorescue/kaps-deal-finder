"""eBay via the official Browse API (free tier ~5,000 calls/day).

Setup (one-time, ~10 min):
  1. Register at https://developer.ebay.com (free)
  2. Create an app -> copy the PRODUCTION App ID (client id) and Cert ID
     (client secret)
  3. Put them in .env as EBAY_CLIENT_ID / EBAY_CLIENT_SECRET

No keys yet? This source just logs a note and returns nothing.
"""
from __future__ import annotations

import logging
import os

import requests

from .base import Listing

log = logging.getLogger("dealfinder.ebay")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
CATEGORY_VIDEO_GAME_CONSOLES = "139971"

_token_cache: str | None = None


def _get_token(client_id: str, client_secret: str) -> str:
    """App-level OAuth token (client credentials — no eBay login involved)."""
    global _token_cache
    if _token_cache:
        return _token_cache
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    _token_cache = resp.json()["access_token"]
    return _token_cache


def fetch(consoles: dict, settings: dict) -> list[Listing]:
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log.info("eBay skipped: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set in .env")
        return []

    token = _get_token(client_id, client_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    limit = settings["sources"]["ebay"].get("max_results_per_search", 50)

    listings: list[Listing] = []
    seen_ids: set[str] = set()  # same item can surface for multiple searches

    for key, cfg in consoles.items():
        for term in cfg.get("search_terms", []):
            try:
                resp = requests.get(
                    SEARCH_URL,
                    headers=headers,
                    params={
                        "q": term,
                        "category_ids": CATEGORY_VIDEO_GAME_CONSOLES,
                        "sort": "newlyListed",
                        "limit": str(limit),
                        "filter": "priceCurrency:USD",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                log.warning("eBay search failed for %r: %s", term, e)
                continue

            for item in resp.json().get("itemSummaries", []):
                item_id = item.get("itemId", "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                price = None
                try:
                    price = float(item["price"]["value"])
                except (KeyError, TypeError, ValueError):
                    pass
                listings.append(Listing(
                    source="ebay",
                    listing_id=item_id,
                    title=item.get("title", ""),
                    url=item.get("itemWebUrl", ""),
                    # Browse API's condition string ("For parts or not
                    # working" etc.) is useful signal — fold it into the
                    # text our classifier reads.
                    description=item.get("condition", ""),
                    price=price,
                    price_note="price excludes shipping",
                ))

    log.info("eBay: %d unique listings fetched", len(listings))
    return listings
