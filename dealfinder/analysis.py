"""The scoring engine: match listings to consoles, parse lot quantities,
apply yield rates, detect cheap fixes vs red flags, and tier every listing.

Tiers (driven by profit_tiers in settings.yaml):
  great    -> est. profit >= $50/unit  -> 🔥 instant alert
  good     -> est. profit >= $40/unit  -> ✅ instant alert
  marginal -> est. profit >= $25/unit  -> 🟡 hourly digest only
  skip     -> below marginal           -> stored for dedup, never shown
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sources.base import Listing

# Spelled-out quantities ("Four Nintendo DS Lite consoles")
SPELLED = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
SPELLED_ALT = "|".join(SPELLED)

# Quantity patterns, tried in order on the console-name-STRIPPED title.
# Stripping first is what makes "Lot of 3 Nintendo 3DS" parse as qty 3
# instead of tripping over the 3 in "3DS".
QTY_PATTERNS = [
    re.compile(r"(?:job\s+)?lot\s+of\s+(\d{1,2})\b"),
    re.compile(rf"(?:job\s+)?lot\s+of\s+({SPELLED_ALT})\b"),
    re.compile(r"(?:job\s+)?lot\s+(\d{1,2})\b"),            # "Lot 10 DS Lite"
    re.compile(r"bundle\s+(?:of\s+)?(\d{1,2})\b"),
    re.compile(r"set\s+of\s+(\d{1,2})\b"),
    re.compile(r"\((\d{1,2})\)"),                            # "(5) Broken..."
    re.compile(r"\b(\d{1,2})\s*[x×]\b"),                     # "4x ..."
    re.compile(r"\b[x×]\s*(\d{1,2})\b"),                     # "... x4"
    re.compile(r"\b(\d{1,2})\s+(?:consoles?|systems?|units?|handhelds?)\b"),
    re.compile(rf"\b({SPELLED_ALT})\s+(?:\w+\s+){{0,4}}?(?:consoles?|systems?|units?|handhelds?)\b"),
    re.compile(r"[-–—]\s*(\d{1,2})\s+(?:consoles?|systems?|units?)\b"),
]
LOT_HINT = re.compile(r"\b(lot|bundle|joblot|wholesale|pallet|collection|reseller)\b")
MAX_SANE_QTY = 30
# "Bundle 15 Games" = 1 console + 15 games, NOT 15 consoles. Blank out
# "<N> games/carts/manuals/styluses" before hunting for lot quantities.
NON_CONSOLE_COUNTS = re.compile(
    r"\b\d{1,3}\s+(?:games?|carts?|cartridges?|manuals?|stylus(?:es)?|cases?|chargers?)\b")


@dataclass
class Analysis:
    """Everything we figured out about one listing."""
    listing: Listing
    consoles: list[str] = field(default_factory=list)
    excluded_reason: str = ""     # non-empty = drop silently (accessory/avoid)
    quantity: int = 1
    qty_uncertain: bool = False   # lot-ish words but no parseable count
    mixed_lot: bool = False       # multiple console types detected
    condition: str = "unverified"
    yield_rate: float = 1.0       # 1.0 for single units; lots get settings yield
    shipping: float | None = None
    total_cost: float | None = None
    raw_per_unit: float | None = None
    parts_credit: float = 0.0
    eff_per_unit: float | None = None
    sell_price: int = 0           # cheapest matched console (conservative)
    est_profit_unit: float | None = None
    est_profit_total: float | None = None
    good_units: int = 1           # expected salvageable units
    tier: str = "skip"            # great | good | marginal | skip | no_price
    cheap_fixes: list[str] = field(default_factory=list)
    screen_issue: bool = False
    signals: list[str] = field(default_factory=list)
    ending_soon: bool = False

    @property
    def primary_console(self) -> str | None:
        return self.consoles[0] if self.consoles else None


def _found(text: str, phrases: list[str]) -> list[str]:
    hits = [p for p in phrases if p in text]
    # Drop phrases that are substrings of a longer match ("hinge" vs
    # "broken hinge") so alert notes read cleanly.
    return [h for h in hits
            if not any(h != o and h in o for o in hits)]


def match_consoles(text: str, consoles_cfg: dict) -> list[str]:
    matches = []
    for key, cfg in consoles_cfg.items():
        if not cfg.get("enabled", True):
            continue
        if any(kw in text for kw in cfg["keywords"]):
            if not any(ex in text for ex in cfg.get("exclude", [])):
                matches.append(key)
    return matches


def strip_console_names(text: str, consoles_cfg: dict) -> str:
    """Remove console names & model numbers so their digits can't be
    mistaken for lot quantities (the '3' in '3DS', the 101 in 'AGS-101')."""
    tokens: set[str] = set()
    for cfg in consoles_cfg.values():
        tokens.update(cfg.get("keywords", []))
        tokens.update(cfg.get("model_numbers", []))
    tokens.update(["new nintendo 3ds xl", "new nintendo 2ds xl", "nintendo",
                   "3ds xl", "2ds xl", "3ds", "2ds", "dsi xl", "dsi",
                   "ds lite", "dslite", "ds"])
    for tok in sorted(tokens, key=len, reverse=True):  # longest first
        text = text.replace(tok, " ")
    return text


def extract_quantity(stripped: str) -> tuple[int, bool]:
    """(quantity, uncertain). Uncertain = lot words present but no count —
    default to 1 and flag for manual review rather than guessing."""
    stripped = NON_CONSOLE_COUNTS.sub(" ", stripped)
    for pat in QTY_PATTERNS:
        m = pat.search(stripped)
        if m:
            raw = m.group(1)
            qty = SPELLED.get(raw, None) or (int(raw) if raw.isdigit() else 0)
            if 2 <= qty <= MAX_SANE_QTY:
                return qty, False
    if LOT_HINT.search(stripped):
        return 1, True
    return 1, False


def determine_yield(text: str, quantity: int, ycfg: dict) -> float:
    """Positive seller claims beat negative ones; among negatives the worst
    (lowest) matching yield wins. Single units don't get yield-adjusted."""
    if quantity < 2:
        return 1.0
    if any(p in text for p in ycfg["positive"]["phrases"]):
        return ycfg["positive"]["rate"]
    rates = [rule["rate"] for rule in ycfg["negative"]
             if any(p in text for p in rule["phrases"])]
    return min(rates) if rates else ycfg["default"]


def classify_condition(text: str, condition_cfg: dict) -> str:
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


def max_buy(sell_price: int, tier_profit: float, pricing: dict) -> float:
    return round(sell_price - pricing["refurb_cost"] - tier_profit, 2)


def analyze(listing: Listing, consoles_cfg: dict, settings: dict) -> Analysis:
    text = f"{listing.title} {listing.description}".lower()
    result = Analysis(listing=listing)
    kw = settings["keywords"]
    pricing = settings["pricing"]

    # 1. Hard exclusions: accessories, and damage not worth the bench time
    hit = _found(text, settings["matching"]["exclude_always"])
    if hit:
        result.excluded_reason = f"accessory: {hit[0]}"
        return result
    hit = _found(text, kw["avoid"])
    if hit:
        result.excluded_reason = f"red flag: {hit[0]}"
        return result

    # 2. Which console(s)?
    result.consoles = match_consoles(text, consoles_cfg)
    if not result.consoles:
        return result
    result.mixed_lot = len(result.consoles) > 1
    # Conservative: value mixed lots at the CHEAPEST matched console's price
    result.sell_price = min(consoles_cfg[k]["sell_price"] for k in result.consoles)

    # 3. Quantity (strip console names/model numbers first)
    stripped = strip_console_names(text, consoles_cfg)
    result.quantity, result.qty_uncertain = extract_quantity(stripped)

    # 4. Condition language, yield, fix/flag/signal keywords
    result.condition = classify_condition(text, settings["condition"])
    result.yield_rate = determine_yield(text, result.quantity, settings["yield"])
    result.cheap_fixes = _found(text, kw["cheap_fixes"])
    result.screen_issue = bool(_found(text, kw["screen_issues"]))
    result.signals = _found(text, kw["underpriced_signals"])
    # Thin-listing signals: sloppy titles often mean non-expert sellers
    title = listing.title.strip()
    if len(title.split()) < 5:
        result.signals.append("very short title")
    elif title.isupper():
        result.signals.append("all-caps title")

    # 5. The money math
    if listing.price is None:
        result.tier = "no_price"
        return result

    result.shipping = listing.shipping
    result.total_cost = round(listing.price + (listing.shipping or 0), 2)
    result.raw_per_unit = round(result.total_cost / result.quantity, 2)
    if result.quantity >= 2:
        duds = result.quantity * (1 - result.yield_rate)
        # Cap the credit at half the lot cost — parts value should sweeten a
        # deal, never turn a $40 charger lot into "free consoles"
        result.parts_credit = round(min(
            duds * pricing["parts_credit_per_dud"],
            result.total_cost * 0.5), 2)
        good = result.quantity * result.yield_rate
    else:
        good = 1.0
    result.good_units = max(1, round(good))
    result.eff_per_unit = max(0.01, round(
        (result.total_cost - result.parts_credit) / good, 2))

    # 6. Estimated profit & tier
    profit = result.sell_price - pricing["refurb_cost"] - result.eff_per_unit
    if result.screen_issue:
        profit -= pricing["screen_repair_penalty"]
    result.est_profit_unit = round(profit, 2)
    result.est_profit_total = round(profit * result.good_units, 2)

    tiers = pricing["profit_tiers"]
    if profit >= tiers["great"]:
        result.tier = "great"
    elif profit >= tiers["good"]:
        result.tier = "good"
    elif profit >= tiers["marginal"]:
        result.tier = "marginal"
    else:
        result.tier = "skip"

    result.ending_soon = listing.ending_soon
    return result
