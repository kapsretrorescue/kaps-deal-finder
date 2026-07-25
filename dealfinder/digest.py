"""Format alerts and the hourly family digest for Discord.

Instant alert format (per the spec):

  🔥 GREAT DEAL — 4x DS Lite
  "Lot of 4 Nintendo DS Lite For Parts Broken Hinges"
  Price: $99.00 + $15.00 ship = $114.00 total
  Raw/unit: $28.50 · Effective (75% yield, −$10.00 parts credit): $34.67
  Max buy (@$25/$40/$50 profit): $52 / $37 / $27
  Est. profit: ~$39/good unit · ~$117 total (3 units)
  Issues: broken hinge (cheap fix) · Signals: estate sale
  ⏰ AUCTION ENDING SOON
  [ebay] https://...
"""
from __future__ import annotations

from datetime import datetime, timezone

TIER_LABELS = {
    "great": "🔥 GREAT DEAL",
    "good": "✅ GOOD DEAL",
    "marginal": "🟡 MARGINAL",
    "no_price": "👀 NO PRICE",
    "skip": "❓",
}
CONDITION_LABELS = {
    "parts": "for parts", "working": "tested working",
    "mixed": "mixed signals", "unverified": "condition unverified",
}


def _console_names(row, consoles_cfg: dict) -> str:
    keys = (row["consoles"] or "").split(",")
    return ", ".join(consoles_cfg[k]["name"] for k in keys if k in consoles_cfg)


def _time_left(end_time: str) -> str:
    try:
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        mins = int((end - datetime.now(timezone.utc)).total_seconds() // 60)
        if mins <= 0:
            return "ended"
        return f"{mins // 60}h {mins % 60}m"
    except (ValueError, AttributeError):
        return "?"


def _maxes(row, consoles_cfg: dict, pricing: dict) -> str:
    keys = [k for k in (row["consoles"] or "").split(",") if k in consoles_cfg]
    if not keys:
        return ""
    sell = min(consoles_cfg[k]["sell_price"] for k in keys)
    r = pricing["refurb_cost"]
    t = pricing["profit_tiers"]
    return (f"${sell - r - t['marginal']:.0f} / ${sell - r - t['good']:.0f} / "
            f"${sell - r - t['great']:.0f}")


def format_row(row, consoles_cfg: dict, pricing: dict) -> str:
    qty = row["quantity"] or 1
    head = f"{TIER_LABELS.get(row['tier'], row['tier'])} — "
    head += f"{qty}x " if qty > 1 else ""
    head += _console_names(row, consoles_cfg)
    if row["mixed_lot"]:
        head += "  ⚠ MIXED — verify contents"
    if row["qty_uncertain"]:
        head += "  ⚠ QTY UNCERTAIN — verify"

    lines = [f"**{head}**", row["title"]]

    if row["price"] is not None:
        ship = (f" + ${row['shipping']:.2f} ship" if row["shipping"] is not None
                else " + ?? ship")
        lines.append(f"Price: ${row['price']:.2f}{ship} = "
                     f"${row['total_cost']:.2f} total ({CONDITION_LABELS.get(row['condition'], '')})")
        eff = f"Raw/unit: ${row['per_unit']:.2f}"
        if qty > 1:
            eff += (f" · Effective ({row['yield_rate']:.0%} yield"
                    + (f", −${row['parts_credit']:.2f} parts credit"
                       if row["parts_credit"] else "")
                    + f"): ${row['eff_per_unit']:.2f}")
        lines.append(eff)
        lines.append(f"Max buy (@$25/$40/$50 profit): {_maxes(row, consoles_cfg, pricing)}")
        profit = f"Est. profit: ~${row['est_profit']:.0f}/good unit"
        if qty > 1:
            profit += (f" · ~${row['est_profit_total']:.0f} total "
                       f"({row['good_units']} units)")
        if row["screen_issue"]:
            profit += " (screen repair −$45 applied)"
        lines.append(profit)
    else:
        lines.append(row["price_note"] or "no price parsed — open and check")

    notes = []
    if row["cheap_fixes"]:
        notes.append(f"Issues: {row['cheap_fixes'].replace(',', ', ')} (cheap fix)")
    if row["signals"]:
        notes.append(f"Signals: {row['signals'].replace(',', ', ')}")
    if row["seller_feedback"]:
        notes.append(f"Seller: {row['seller_feedback']}")
    if notes:
        lines.append(" · ".join(notes))

    if row["listing_type"] == "AUCTION" and row["end_time"]:
        left = _time_left(row["end_time"])
        marker = "⏰ **AUCTION ENDS " + left + "**" if row["ending_notified"] \
            else f"Auction ends {left}"
        lines.append(marker)

    lines.append(f"[{row['source']}] {row['url']}")
    return "\n".join(lines)


def build_instant(rows, consoles_cfg: dict, pricing: dict) -> str:
    header = f"## 🚨 {len(rows)} deal(s) worth acting on\n"
    return "\n\n".join([header] + [format_row(r, consoles_cfg, pricing) for r in rows])


def build_digest(rows, consoles_cfg: dict, settings: dict) -> str:
    """Hourly market pulse: the best 3-6 recent listings per console family."""
    families = consoles_cfg["_families"]
    pricing = settings["pricing"]
    cap = settings["digest"]["per_family_max"]
    consoles = {k: v for k, v in consoles_cfg.items() if k != "_families"}

    console_to_family = {}
    for fam_key, fam in families.items():
        for c in fam["consoles"]:
            console_to_family[c] = fam_key

    sections = []
    shown = set()
    for fam_key, fam in families.items():
        fam_rows = []
        for r in rows:
            primary = (r["consoles"] or "").split(",")[0]
            if console_to_family.get(primary) == fam_key and r["tier"] != "no_price":
                fam_rows.append(r)
        fam_rows = fam_rows[:cap]
        if fam_rows:
            sections.append(f"## 🎮 {fam['name']} — best of the last "
                            f"{settings['digest']['lookback_hours']}h\n")
            for r in fam_rows:
                sections.append(format_row(r, consoles, pricing))
                shown.add((r["source"], r["listing_id"]))

    # Possible lots where we couldn't parse a count: a "DS Lite Console Lot"
    # priced like 3 units looks terrible as 1 unit — human eyes needed.
    uncertain = [r for r in rows if r["qty_uncertain"]
                 and (r["source"], r["listing_id"]) not in shown][:5]
    if uncertain:
        sections.append("\n## ❓ Possible lots — verify quantity manually\n")
        for r in uncertain:
            sections.append(format_row(r, consoles, pricing))
            shown.add((r["source"], r["listing_id"]))

    unpriced = [r for r in rows if r["tier"] == "no_price"
                and (r["source"], r["listing_id"]) not in shown][:5]
    if unpriced:
        sections.append("\n## 👀 Matches with no clean price\n")
        sections.extend(format_row(r, consoles, pricing) for r in unpriced)

    return "\n\n".join(sections)
