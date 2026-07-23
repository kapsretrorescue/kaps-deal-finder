"""The brains: match listings to consoles, parse lot quantity, classify
condition language, and decide whether something is a deal.

Everything tunable lives in config/consoles.yaml and config/settings.yaml —
this file should rarely need editing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sources.base import Listing

# Patterns that suggest a multi-unit lot, tried in order; first hit wins.
QTY_PATTERNS = [
    re.compile(r"lot\s+of\s+(\d{1,2})"),
    re.compile(r"bundle\s+of\s+(\d{1,2})"),
    re.compile(r"set\s+of\s+(\d{1,2})"),
    re.compile(r"\b(\d{1,2})\s*[x×]\s"),        # "5x game boy"
    re.compile(r"\bx\s?(\d{1,2})\b"),            # "game boy x5"
    re.compile(r"\b(\d{1,2})\s+(?:consoles|systems|units|handhelds|game\s?boys)\b"),
]
MAX_SANE_QTY = 30  # anything above this is probably a model number, not a lot size


@dataclass
class Analysis:
    """Everything we figured out about one listing."""
    listing: Listing
    consoles: list[str] = field(default_factory=list)  # matched console keys
    quantity: int = 1
    per_unit: float | None = None
    condition: str = "unverified"   # "parts" | "working" | "mixed" | "unverified"
    is_deal: bool = False
    max_buy: float | None = None    # the threshold that applied
    est_profit: float | None = None  # rough per-unit profit if bought at per_unit

    @property
    def primary_console(self) -> str | None:
        return self.consoles[0] if self.consoles else None


def match_consoles(text: str, consoles_cfg: dict) -> list[str]:
    """Return console keys whose keywords appear in text (minus exclusions).

    A lot listing ("Game Boy Color + DS Lite bundle") can match several.
    """
    matches = []
    for key, cfg in consoles_cfg.items():
        if any(kw in text for kw in cfg["keywords"]):
            if not any(ex in text for ex in cfg.get("exclude", [])):
                matches.append(key)
    return matches


def extract_quantity(text: str) -> int:
    for pat in QTY_PATTERNS:
        m = pat.search(text)
        if m:
            qty = int(m.group(1))
            if 2 <= qty <= MAX_SANE_QTY:
                return qty
    return 1


def classify_condition(text: str, condition_cfg: dict) -> str:
    """'parts', 'working', 'mixed', or 'unverified'.

    Parts phrases are blanked out of the text before we look for working
    phrases, so "not working" never counts as "working".
    """
    parts_hit = False
    for phrase in condition_cfg["parts_phrases"]:
        if phrase in text:
            parts_hit = True
            text = text.replace(phrase, " ")
    working_hit = any(p in text for p in condition_cfg["working_phrases"])

    if parts_hit and working_hit:
        return "mixed"
    if parts_hit:
        return "parts"
    if working_hit:
        return "working"
    return "unverified"


def max_buy_price(console_cfg: dict, condition: str, pricing: dict) -> float:
    """Most you can pay per unit and still clear min_profit after fees.

    working                -> only light refurb needed
    parts/mixed/unverified -> assume a full repair (stricter, safer)
    """
    net_resale = console_cfg["resale"] * (1 - pricing["fee_rate"])
    if condition == "working":
        cost_to_flip = pricing["refurb_cost_working"]
    else:
        cost_to_flip = console_cfg["repair_cost"]
    return round(net_resale - cost_to_flip - pricing["min_profit"], 2)


def analyze(listing: Listing, consoles_cfg: dict, settings: dict) -> Analysis:
    text = f"{listing.title} {listing.description}".lower()

    result = Analysis(listing=listing)
    result.consoles = match_consoles(text, consoles_cfg)
    if not result.consoles:
        return result  # not one of ours — caller will drop it

    result.quantity = extract_quantity(text)
    result.condition = classify_condition(text, settings["condition"])

    if listing.price is not None:
        result.per_unit = round(listing.price / result.quantity, 2)
        # Judge against the best-value console matched (highest max-buy),
        # since in a mixed lot that's the unit carrying the value.
        pricing = settings["pricing"]
        thresholds = [
            max_buy_price(consoles_cfg[key], result.condition, pricing)
            for key in result.consoles
        ]
        result.max_buy = max(thresholds)
        result.is_deal = result.per_unit <= result.max_buy
        # est_profit = headroom below max_buy plus the min_profit floor
        result.est_profit = round(result.max_buy - result.per_unit + pricing["min_profit"], 2)

    return result
