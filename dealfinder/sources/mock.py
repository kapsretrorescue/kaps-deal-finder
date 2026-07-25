"""Fake listings exercising the scoring engine's tricky cases.

Run:  python -m dealfinder.main --mock --dry-run
"""
from __future__ import annotations

from .base import Listing


def fetch(consoles: dict, settings: dict) -> list[Listing]:
    return [
        # The spec's worked example: 4x DS Lite $99 + $15 ship, for parts w/
        # broken hinges -> hinge yield 0.90, parts credit, should be GREAT/GOOD
        Listing(
            source="mock", listing_id="mock-101",
            title="Lot of 4 Nintendo DS Lite For Parts Broken Hinges",
            url="https://example.com/101", price=99.00, shipping=15.00,
            description="All have broken hinges, otherwise power on fine.",
        ),
        # Parsing trap: "Lot of 3 Nintendo 3DS" -> qty 3, NOT qty 3DS
        Listing(
            source="mock", listing_id="mock-102",
            title="Lot of 3 Nintendo 3DS untested estate sale find",
            url="https://example.com/102", price=210.00, shipping=20.00,
        ),
        # "(5)" pattern + cheap fixes
        Listing(
            source="mock", listing_id="mock-103",
            title="(5) Broken Nintendo DS Lite Systems yellowed sticky buttons",
            url="https://example.com/103", price=110.00, shipping=12.00,
            description="Untested, from a storage unit cleanout.",
        ),
        # Water damage -> excluded entirely
        Listing(
            source="mock", listing_id="mock-104",
            title="Nintendo DSi water damage for parts",
            url="https://example.com/104", price=5.00, shipping=5.00,
        ),
        # Accessory noise -> excluded
        Listing(
            source="mock", listing_id="mock-105",
            title="Game Boy Color shells lot of 6 aftermarket",
            url="https://example.com/105", price=20.00, shipping=5.00,
        ),
        # Screen issue -> $45 penalty should sink it to skip
        Listing(
            source="mock", listing_id="mock-106",
            title="Nintendo DS Lite cracked screen for parts",
            url="https://example.com/106", price=30.00, shipping=8.00,
        ),
        # Spelled-out quantity + auction ending soon
        Listing(
            source="mock", listing_id="mock-107",
            title="Four Game Boy Advance consoles as is grandma estate",
            url="https://example.com/107", price=120.00, shipping=15.00,
            listing_type="AUCTION", end_time="2099-01-01T00:00:00Z",
            ending_soon=True, seller_feedback="98.7% (412)",
        ),
        # Lot words but no count -> qty uncertain flag
        Listing(
            source="mock", listing_id="mock-108",
            title="Nintendo DS Lite Console Lot untested",
            url="https://example.com/108", price=95.00, shipping=10.00,
        ),
        # Single working unit at market price -> should be skip (no alert)
        Listing(
            source="mock", listing_id="mock-109",
            title="Nintendo DS Lite tested working with charger",
            url="https://example.com/109", price=62.00, shipping=5.00,
        ),
    ]
