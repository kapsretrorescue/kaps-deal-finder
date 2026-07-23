"""Fake listings for testing the whole pipeline without any API keys.

Run:  python -m dealfinder.main --mock --dry-run
"""
from __future__ import annotations

from .base import Listing


def fetch(consoles: dict, settings: dict) -> list[Listing]:
    return [
        # Should be a DEAL (parts): GBC well under threshold
        Listing(
            source="mock", listing_id="mock-001",
            title="Game Boy Color - broken screen, for parts or repair",
            url="https://example.com/1", price=18.50,
            description="Powers on but screen is cracked. Sold as-is.",
        ),
        # Working DS Lite at full market price: match, but NOT a deal
        Listing(
            source="mock", listing_id="mock-002",
            title="Nintendo DS Lite white - tested and working great",
            url="https://example.com/2", price=62.00,
            description="Fully functional, comes with charger.",
        ),
        # Multi-unit lot: 5 GBAs for $100 -> $20/unit -> DEAL
        Listing(
            source="mock", listing_id="mock-003",
            title="Lot of 5 Game Boy Advance consoles - untested",
            url="https://example.com/3", price=100.00,
            description="Estate find, all untested, sold as-is.",
        ),
        # hardwareswap-style post with no clean price
        Listing(
            source="mock", listing_id="mock-004",
            title="[USA-TX] [H] New 3DS XL, DSi, games [W] PayPal",
            url="https://example.com/4", price=None,
            description="Prices in comments, everything tested working.",
            price_note="no clean price parsed — open and check",
        ),
        # Not our console at all -> should be silently dropped
        Listing(
            source="mock", listing_id="mock-005",
            title="Sony PSP 3000 for parts",
            url="https://example.com/5", price=25.00,
        ),
        # Mixed condition language: works but cracked hinge
        Listing(
            source="mock", listing_id="mock-006",
            title="GBA SP AGS-101 tested working but cracked hinge",
            url="https://example.com/6", price=40.00,
            description="Plays fine, hinge is cracked. As is.",
        ),
    ]
