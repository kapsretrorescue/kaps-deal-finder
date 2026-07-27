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

# Discord embed cards: colour-coded, ~4 lines each on a phone instead of 8.
TIER_COLORS = {
    "great": 0xFF4757,     # hot red
    "good": 0x2ED573,      # green
    "marginal": 0xFFA502,  # amber
    "no_price": 0x747D8C,  # grey
    "skip": 0x747D8C,
}
TIER_EMOJI = {"great": "🔥", "good": "✅", "marginal": "🟡",
              "no_price": "👀", "skip": "❓"}


def row_to_embed(row, consoles_cfg: dict, pricing: dict,
                 was_price: float | None = None) -> dict:
    """One listing as a compact Discord embed card.

    Only the numbers you act on: what it costs, what you'd clear, and what's
    wrong with it. Static reference (max-buy tiers) lives in `!consoles`.
    """
    qty = row["quantity"] or 1
    name = _console_names(row, consoles_cfg)
    emoji = TIER_EMOJI.get(row["tier"], "")

    # Price cut and local pickup both change how urgent a card is, so they
    # lead the title rather than hiding in the footer.
    prefix = "📉 " if was_price else ("🚗 " if row["local_pickup"] else "")
    if row["price"] is None:
        headline = f"{prefix}{emoji} {name} · price?"
    elif qty > 1:
        headline = f"{prefix}{emoji} {qty}x {name} · ${row['eff_per_unit']:.0f}/unit"
    else:
        headline = f"{prefix}{emoji} {name} · ${row['price']:.0f}"

    fields = []
    if was_price:
        fields.append({"name": "Was", "value": f"~~${was_price:.0f}~~",
                       "inline": True})
    if row["price"] is not None:
        if row["local_pickup"]:
            ship = "pickup"
        elif row["shipping"]:
            ship = f"+${row['shipping']:.0f} ship"
        elif row["shipping"] == 0:
            ship = "free ship"
        else:
            ship = "+? ship"
        fields.append({"name": "Cost", "value": f"${row['price']:.0f} {ship}",
                       "inline": True})
        if row["est_profit"] is not None:
            fields.append({"name": "Profit", "value": f"~${row['est_profit']:.0f}/unit",
                           "inline": True})
        if qty > 1 and row["est_profit_total"] is not None:
            fields.append({"name": "Lot total",
                           "value": f"~${row['est_profit_total']:.0f} · "
                                    f"{row['good_units']} good",
                           "inline": True})

    # Footer: the caveats, compressed
    bits = [CONDITION_LABELS.get(row["condition"], "")]
    if row["cheap_fixes"]:
        fixes = row["cheap_fixes"].split(",")[:2]
        bits.append("cheap fix: " + ", ".join(fixes))
    if row["screen_issue"]:
        bits.append("screen −$45")
    if row["mixed_lot"]:
        bits.append("⚠ mixed lot")
    if row["qty_uncertain"]:
        bits.append("⚠ verify qty")
    if row["signals"]:
        bits.append(row["signals"].split(",")[0])
    if row["listing_type"] == "AUCTION" and row["end_time"]:
        bits.append(f"⏰ ends {_time_left(row['end_time'])}")
    if row["seller_feedback"]:
        bits.append(row["seller_feedback"])

    return {
        "title": headline[:250],
        "url": row["url"],
        "description": f"_{row['title'][:110]}_",
        "color": TIER_COLORS.get(row["tier"], 0x747D8C),
        "fields": fields,
        "footer": {"text": " · ".join(b for b in bits if b)[:2040]},
    }


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
            n = row["good_units"]
            profit += (f" · ~${row['est_profit_total']:.0f} total "
                       f"({n} good unit{'s' if n != 1 else ''} expected)")
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


def embeds_instant(rows, consoles_cfg: dict, pricing: dict,
                   drop_prices: dict | None = None) -> tuple[str, list]:
    drop_prices = drop_prices or {}
    n = len(rows)
    drops = sum(1 for r in rows if r["listing_id"] in drop_prices)
    head = f"🚨 **{n} deal{'s' if n != 1 else ''} worth acting on**"
    if drops:
        head += f" · {drops} price cut{'s' if drops != 1 else ''} 📉"
    return head, [row_to_embed(r, consoles_cfg, pricing,
                               drop_prices.get(r["listing_id"])) for r in rows]


def embeds_digest(rows, consoles_cfg: dict, settings: dict) -> tuple[str, list]:
    """Scheduled pulse: just the top N cards, newest window."""
    d = settings["digest"]
    consoles = {k: v for k, v in consoles_cfg.items() if k != "_families"}
    ranked = [r for r in rows if r["tier"] != "no_price"][:d.get("top_n", 10)]
    if not ranked:
        return "", []

    extras = [r for r in rows if r["qty_uncertain"]
              and r not in ranked][:3]
    head = f"🏆 **Top {len(ranked)}** · last {d['lookback_hours']}h"
    if extras:
        # One line, not three cards — these only need a glance
        links = " ".join(f"[#{i+1}]({r['url']})" for i, r in enumerate(extras))
        head += f"\n❓ possible lots, qty unclear: {links}"
    return head, [row_to_embed(r, consoles, settings["pricing"]) for r in ranked]


def embeds_search(rows, console_name: str, consoles_cfg: dict,
                  pricing: dict) -> tuple[str, list]:
    if not rows:
        return (f"🔍 **{console_name}** — nothing matched right now.", [])
    deals = sum(1 for r in rows if r["tier"] in ("great", "good", "marginal"))
    head = (f"🔍 **{console_name}** · {deals} deal{'s' if deals != 1 else ''} "
            f"in the {len(rows)} best listings" if deals else
            f"🔍 **{console_name}** · no deals right now — closest listings")
    return head, [row_to_embed(r, consoles_cfg, pricing) for r in rows]


def build_search(rows, console_name: str, consoles_cfg: dict, pricing: dict) -> str:
    """Results of an on-demand `!search <console>`, best profit first.

    Unlike alerts this shows what's out there even when nothing clears the
    profit bar — 'nothing good right now' is useful information too.
    """
    if not rows:
        return (f"🔍 **{console_name}** — nothing on eBay matched right now.\n"
                "_Try again later, or check the console's `search_terms` in "
                "`consoles.yaml` if this keeps coming up empty._")

    deals = [r for r in rows if r["tier"] in ("great", "good", "marginal")]
    header = (f"## 🔍 {console_name} — {len(deals)} deal(s) in the "
              f"{len(rows)} best current listings\n") if deals else (
              f"## 🔍 {console_name} — no deals right now\n"
              "_Nothing clears your minimum profit. Closest listings:_\n")
    return "\n\n".join(
        [header] + [f"**#{i}** · " + format_row(r, consoles_cfg, pricing)
                    for i, r in enumerate(rows, 1)])


def build_digest(rows, consoles_cfg: dict, settings: dict) -> str:
    """Scheduled market pulse: the N best deals across every console.

    `rows` arrives already ranked (great -> good -> marginal, then by total
    estimated profit), so the top slice is simply the best of the window.
    """
    pricing = settings["pricing"]
    d = settings["digest"]
    top_n = d.get("top_n", 10)
    consoles = {k: v for k, v in consoles_cfg.items() if k != "_families"}

    ranked = [r for r in rows if r["tier"] != "no_price"][:top_n]

    sections = []
    shown = set()
    if ranked:
        sections.append(f"## 🏆 Top {len(ranked)} deals — last "
                        f"{d['lookback_hours']}h, all consoles\n")
        for i, r in enumerate(ranked, 1):
            sections.append(f"**#{i}** · " + format_row(r, consoles, pricing))
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
