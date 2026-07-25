"""Shared data shape for every marketplace source.

Every source module (ebay.py, reddit.py, mock.py) exposes one function:

    fetch(consoles: dict, settings: dict) -> list[Listing]

To add a new marketplace, copy mock.py and enable it in settings.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Listing:
    source: str                  # "ebay", "reddit", ...
    listing_id: str              # unique ID within that source (dedup key)
    title: str
    url: str
    description: str = ""
    price: float | None = None   # None = no clean price found
    price_note: str = ""
    shipping: float | None = None    # None = shipping cost unknown
    listing_type: str = ""       # "AUCTION", "FIXED_PRICE", or ""
    end_time: str = ""           # ISO timestamp for auctions, else ""
    ending_soon: bool = False    # auction ends within the configured window
    seller_feedback: str = ""    # e.g. "99.1% (2345)"
