"""eBay via the official Browse API (free tier ~5,000 calls/day).

Two passes per console per run:
  1. sort=newlyListed  — catch fresh listings early
  2. auctions sorted by soonest ending — so underbid auctions in their final
     window (default 2h) can be re-alerted at their CURRENT price

Keys come from .env / GitHub secrets (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET).
Missing keys = source skips itself with a log line.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from .base import Listing

log = logging.getLogger("dealfinder.ebay")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
CATEGORY_VIDEO_GAME_CONSOLES = "139971"

_token_cache: str | None = None


def _get_token(client_id: str, client_secret: str) -> str:
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


def _parse_item(item: dict, ending_cutoff: datetime) -> Listing | None:
    item_id = item.get("itemId", "")
    if not item_id:
        return None

    price = None
    try:
        price = float(item["price"]["value"])
    except (KeyError, TypeError, ValueError):
        pass

    # Shipping: take the first quoted option; None = unknown (freight/varies)
    shipping = None
    note = "shipping unknown"
    for opt in item.get("shippingOptions", []):
        cost = opt.get("shippingCost", {})
        try:
            shipping = float(cost.get("value"))
            note = ""
            break
        except (TypeError, ValueError):
            continue

    buying = item.get("buyingOptions", [])
    listing_type = "AUCTION" if "AUCTION" in buying else (
        "FIXED_PRICE" if "FIXED_PRICE" in buying else "")

    end_time = item.get("itemEndDate", "")
    ending_soon = False
    if listing_type == "AUCTION" and end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            ending_soon = end_dt <= ending_cutoff
        except ValueError:
            pass

    feedback = ""
    seller = item.get("seller", {})
    if seller.get("feedbackPercentage"):
        feedback = f"{seller['feedbackPercentage']}% ({seller.get('feedbackScore', '?')})"

    # For auctions, "price" is the CURRENT bid — flag that in the note
    if listing_type == "AUCTION":
        note = (note + " · " if note else "") + "auction — price is current bid"

    return Listing(
        source="ebay",
        listing_id=item_id,
        title=item.get("title", ""),
        url=item.get("itemWebUrl", ""),
        description=item.get("condition", ""),
        price=price,
        price_note=note,
        shipping=shipping,
        listing_type=listing_type,
        end_time=end_time,
        ending_soon=ending_soon,
        seller_feedback=feedback,
    )


def fetch(consoles: dict, settings: dict) -> list[Listing]:
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log.info("eBay skipped: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set in .env")
        return []

    cfg = settings["sources"]["ebay"]
    token = _get_token(client_id, client_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    limit = str(cfg.get("max_results_per_search", 50))
    ending_cutoff = datetime.now(timezone.utc) + timedelta(
        hours=cfg.get("ending_soon_hours", 2))

    listings: list[Listing] = []
    seen_ids: set[str] = set()

    def run_search(params: dict) -> None:
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("eBay search failed (%s): %s", params.get("q"), e)
            return
        for item in resp.json().get("itemSummaries", []):
            parsed = _parse_item(item, ending_cutoff)
            if parsed and parsed.listing_id not in seen_ids:
                seen_ids.add(parsed.listing_id)
                listings.append(parsed)

    for key, c in consoles.items():
        if not c.get("enabled", True):
            continue
        terms = c.get("search_terms", [])
        # Pass 1: newest listings for every search term
        for term in terms:
            run_search({
                "q": term,
                "category_ids": CATEGORY_VIDEO_GAME_CONSOLES,
                "sort": "newlyListed",
                "limit": limit,
                "filter": "priceCurrency:USD",
            })
        # Pass 2: auctions ending soonest (first term only, to save quota)
        if terms:
            run_search({
                "q": terms[0],
                "category_ids": CATEGORY_VIDEO_GAME_CONSOLES,
                "sort": "endingSoonest",
                "limit": "25",
                "filter": "priceCurrency:USD,buyingOptions:{AUCTION}",
            })

    log.info("eBay: %d unique listings fetched", len(listings))
    return listings
