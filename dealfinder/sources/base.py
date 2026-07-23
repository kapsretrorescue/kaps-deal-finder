"""Shared data shape for every marketplace source.

Every source module (ebay.py, reddit.py, mock.py, and any you add later)
exposes one function:

    fetch(consoles: dict, settings: dict) -> list[Listing]

That's the entire contract. To add a new marketplace, copy mock.py,
make fetch() return Listing objects, and enable it in settings.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Listing:
    source: str            # "ebay", "reddit", ...
    listing_id: str        # unique ID within that source (used for dedup)
    title: str
    url: str
    description: str = ""  # body text if the source provides one
    price: float | None = None  # None = we couldn't find a clean price
    price_note: str = ""   # e.g. "multiple prices in post - open listing"
