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
import re
from datetime import datetime, timedelta, timezone

import requests

from .base import Listing

log = logging.getLogger("dealfinder.ebay")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
CATEGORY_VIDEO_GAME_CONSOLES = "139971"
HTML_TAG = re.compile(r"<[^>]+>")

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
    if resp.status_code >= 300:
        # Expired or revoked credentials are the most likely silent killer:
        # searches quietly return nothing and it looks like a quiet market.
        raise RuntimeError(
            f"eBay auth failed ({resp.status_code}) — check EBAY_CLIENT_ID / "
            f"EBAY_CLIENT_SECRET: {resp.text[:120]}")
    _token_cache = resp.json()["access_token"]
    return _token_cache


def _parse_item(item: dict, ending_cutoff: datetime,
                local_pickup: bool = False) -> Listing | None:
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

    feedback, seller_pct, seller_score = "", None, None
    seller = item.get("seller", {})
    if seller.get("feedbackPercentage"):
        feedback = f"{seller['feedbackPercentage']}% ({seller.get('feedbackScore', '?')})"
        try:
            seller_pct = float(seller["feedbackPercentage"])
        except (TypeError, ValueError):
            pass
    try:
        seller_score = int(seller.get("feedbackScore"))
    except (TypeError, ValueError):
        pass

    # Collecting in person means no postage at all — force it to zero rather
    # than leaving it unknown, so the per-unit maths reflects reality.
    if local_pickup:
        shipping = 0.0
        note = "local pickup"

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
        seller_score=seller_score,
        seller_pct=seller_pct,
        best_offer="BEST_OFFER" in buying,
        local_pickup=local_pickup,
    )


def _headers(settings: dict) -> dict | None:
    """Auth + marketplace + delivery location (needed for shipping quotes)."""
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log.info("eBay skipped: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set in .env")
        return None
    h = {
        "Authorization": f"Bearer {_get_token(client_id, client_secret)}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    zip_code = str(settings["sources"]["ebay"].get("ship_to_zip", "")).strip()
    if zip_code:
        # Without a destination eBay can't calculate shipping, and most
        # listings come back with no shipping cost at all.
        h["X-EBAY-C-ENDUSERCTX"] = (
            f"contextualLocation=country%3DUS%2Czip%3D{zip_code}")
    return h


def fetch_details(item_ids: list[str], settings: dict) -> dict[str, dict]:
    """Full detail for the shortlist that cleared the profit bar.

    Two things only the item endpoint knows:
      · the seller's own description — where "water damage" and "no
        motherboard" usually live, invisible in search results
      · the real item location and whether local pickup is offered.
        eBay's search filters for pickup DO NOT WORK (tested: they return
        sellers in Ohio and Japan unchanged), so pickup is confirmed here
        against actual location data rather than assumed from a filter.
    """
    headers = _headers(settings)
    if not headers or not item_ids:
        return {}
    ecfg = settings["sources"]["ebay"]
    cap = ecfg.get("detail_fetch_max", 40)
    home_state = str(ecfg.get("local_pickup", {}).get("state", "California"))
    out: dict[str, dict] = {}
    local = 0
    for item_id in item_ids[:cap]:
        try:
            resp = requests.get(ITEM_URL + item_id, headers=headers, timeout=30)
            if resp.status_code >= 300:
                continue
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        text = " ".join(filter(None, [
            data.get("shortDescription", ""),
            HTML_TAG.sub(" ", data.get("description", "") or ""),
        ]))
        loc = data.get("itemLocation") or {}
        opts = data.get("shippingOptions") or []
        pickup_offered = any(
            "PICKUP" in str(o.get("shippingCostType", "")).upper()
            or "PICKUP" in str(o.get("type", "")).upper() for o in opts)
        is_local = (loc.get("stateOrProvince") == home_state) and pickup_offered
        if is_local:
            local += 1
        out[item_id] = {
            "description": re.sub(r"\s+", " ", text)[:4000],
            "local_pickup": is_local,
            "where": f"{loc.get('city', '')}, {loc.get('stateOrProvince', '')}".strip(", "),
        }
    log.info("eBay: pulled %d item details (%d genuinely local pickup)",
             len(out), local)
    return out


def fetch(consoles: dict, settings: dict, auctions_only: bool = False) -> list[Listing]:
    headers = _headers(settings)
    if headers is None:
        return []
    cfg = settings["sources"]["ebay"]
    limit = str(cfg.get("max_results_per_search", 50))
    ending_cutoff = datetime.now(timezone.utc) + timedelta(
        hours=cfg.get("ending_soon_hours", 2))

    listings: list[Listing] = []
    seen_ids: set[str] = set()

    def run_search(params: dict, local: bool = False) -> int:
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("eBay search failed (%s): %s", params.get("q"), e)
            return -1                       # -1 = the request itself failed
        found = 0
        for item in resp.json().get("itemSummaries", []):
            parsed = _parse_item(item, ending_cutoff, local_pickup=local)
            found += 1
            if parsed and parsed.listing_id not in seen_ids:
                seen_ids.add(parsed.listing_id)
                listings.append(parsed)
        return found

    # NOTE: there is deliberately no local-pickup search pass. eBay's
    # pickupPostalCode / deliveryOptions filters are documented but silently
    # ignored — tested against the live API, they returned sellers in Ohio,
    # China and Japan unchanged. Pickup is instead confirmed per item in
    # fetch_details(), using the location eBay actually reports.

    for key, c in consoles.items():
        if not c.get("enabled", True):
            continue
        terms = c.get("search_terms", [])
        if not terms:
            continue
        # Auctions ending soonest — the only pass that runs in auctions-only
        # mode, so ending auctions get checked far more often than full scans.
        run_search({
            "q": terms[0],
            "category_ids": CATEGORY_VIDEO_GAME_CONSOLES,
            "sort": "endingSoonest",
            "limit": "25",
            "filter": "priceCurrency:USD,buyingOptions:{AUCTION}",
        })
        if auctions_only:
            continue
        # Newest listings for every search term
        for term in terms:
            run_search({
                "q": term,
                "category_ids": CATEGORY_VIDEO_GAME_CONSOLES,
                "sort": "newlyListed",
                "limit": limit,
                "filter": "priceCurrency:USD",
            })

    log.info("eBay: %d unique listings fetched%s", len(listings),
             " (auctions only)" if auctions_only else "")
    return listings
