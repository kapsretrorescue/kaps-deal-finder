"""Turn pending database rows into human-readable digest text.

Deals are grouped first (ranked by estimated profit), then a second section
of "worth a look" listings that matched a console but had no parseable price.
"""
from __future__ import annotations

CONDITION_LABELS = {
    "parts":      "🔧 FOR PARTS",
    "working":    "✅ TESTED WORKING",
    "mixed":      "⚠️ MIXED SIGNALS",
    "unverified": "❓ CONDITION UNVERIFIED",
}


def _console_names(row, consoles_cfg: dict) -> str:
    keys = (row["consoles"] or "").split(",")
    return ", ".join(consoles_cfg[k]["name"] for k in keys if k in consoles_cfg)


def _deal_line(row, consoles_cfg: dict) -> str:
    qty = row["quantity"] or 1
    lot = f" (lot of {qty} → ${row['per_unit']:.2f}/unit)" if qty > 1 else ""
    note = f" · {row['price_note']}" if row["price_note"] else ""
    return (
        f"**{_console_names(row, consoles_cfg)}** — "
        f"{CONDITION_LABELS.get(row['condition'], row['condition'])}\n"
        f"{row['title']}\n"
        f"${row['price']:.2f}{lot} · max buy ${row['max_buy']:.2f} · "
        f"est. profit ~${row['est_profit']:.2f}/unit{note}\n"
        f"[{row['source']}] {row['url']}"
    )


def _unpriced_line(row, consoles_cfg: dict) -> str:
    return (
        f"**{_console_names(row, consoles_cfg)}** — "
        f"{CONDITION_LABELS.get(row['condition'], row['condition'])}\n"
        f"{row['title']}\n"
        f"{row['price_note'] or 'no price parsed'}\n"
        f"[{row['source']}] {row['url']}"
    )


def build_instant(rows, consoles_cfg: dict) -> str:
    """Short, urgent message for deals worth acting on right now."""
    header = f"## 🚨 Deal alert — {len(rows)} listing(s) beat your max-buy price\n"
    return "\n\n".join([header] + [_deal_line(r, consoles_cfg) for r in rows])


def build(rows, consoles_cfg: dict) -> str:
    """One markdown digest string (works for Discord, plain email, and logs)."""
    deals = [r for r in rows if r["is_deal"]]
    unpriced = [r for r in rows if r["price"] is None]

    parts = []
    if deals:
        parts.append(f"## 🎮 {len(deals)} deal(s) found\n")
        parts.extend(_deal_line(r, consoles_cfg) for r in deals)
    if unpriced:
        parts.append(f"\n## 👀 {len(unpriced)} match(es) with no clean price\n")
        parts.extend(_unpriced_line(r, consoles_cfg) for r in unpriced)
    return "\n\n".join(parts)
