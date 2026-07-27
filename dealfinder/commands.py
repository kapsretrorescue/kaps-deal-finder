"""Discord chat commands for editing config without touching files.

Commands are read from the Discord channel at the START of each run, applied
to the YAML configs, and confirmed with a reply. Because the bot only wakes
up hourly, a command you type now takes effect on the next run.

  !help                          what's available
  !settings                      show current settings
  !consoles                      list consoles, prices, on/off state
  !set <path> <value>            change a setting
  !enable <console>              start watching a console
  !disable <console>             stop watching a console

Examples:
  !set pricing.refurb_cost 42
  !set yield.default 0.8
  !set digest.interval_hours 2
  !set notify.instant_min_tier great
  !set gb_dmg.sell_price 110
  !disable n2ds

Only the paths in SETTABLE below can be changed, each with a sane range —
a typo can't set your refurb cost to $3,800 or turn off every alert.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

log = logging.getLogger("dealfinder.commands")

yaml_rt = YAML()          # round-trip loader: keeps all the comments intact
yaml_rt.preserve_quotes = True

# path -> (file, kind, low, high) | (file, "choice", [options])
SETTABLE: dict[str, tuple] = {
    "pricing.refurb_cost":            ("settings", "num", 0, 200),
    "pricing.profit_tiers.marginal":  ("settings", "num", 0, 500),
    "pricing.profit_tiers.good":      ("settings", "num", 0, 500),
    "pricing.profit_tiers.great":     ("settings", "num", 0, 500),
    "pricing.screen_repair_penalty":  ("settings", "num", 0, 300),
    "pricing.parts_credit_per_dud":   ("settings", "num", 0, 100),
    "yield.default":                  ("settings", "num", 0.1, 1.0),
    "digest.interval_hours":          ("settings", "int", 1, 24),
    "digest.top_n":                   ("settings", "int", 1, 25),
    "digest.search_results":          ("settings", "int", 1, 25),
    "digest.lookback_hours":          ("settings", "int", 1, 168),
    "notify.instant_min_tier":        ("settings", "choice", ["great", "good"]),
    "notify.instant_max_per_run":     ("settings", "int", 1, 25),
    "sources.ebay.ending_soon_hours": ("settings", "int", 1, 48),
    "sources.ebay.max_results_per_search": ("settings", "int", 1, 200),
    "sources.ebay.detail_fetch_max":  ("settings", "int", 0, 100),
    "sources.ebay.ship_to_zip":       ("settings", "zip", None, None),
}
# Per-console: "<console_key>.sell_price"
CONSOLE_FIELDS = {"sell_price": ("num", 1, 2000)}


def normalize(name: str) -> str:
    """'GBA-SP' / 'gba sp' / 'gba_sp' all become 'gbasp'."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def resolve_console(name: str, consoles: dict) -> str | None:
    """Map whatever the user typed to a console key, via key or alias."""
    want = normalize(name)
    if not want:
        return None
    for key, cfg in consoles.items():
        if normalize(key) == want:
            return key
        if any(normalize(a) == want for a in cfg.get("aliases", [])):
            return key
    # Last resort: unique prefix match ("gbas" -> gba_sp)
    hits = [k for k in consoles if normalize(k).startswith(want)]
    return hits[0] if len(hits) == 1 else None


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml_rt.load(f)


def _save(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


def _dig(doc, parts: list[str]):
    node = doc
    for p in parts:
        node = node[p]
    return node


def _coerce(kind, spec, raw: str):
    """Returns (value, error). Range/choice checked here."""
    if kind == "choice":
        if raw.lower() not in spec:
            return None, f"must be one of: {', '.join(spec)}"
        return raw.lower(), None
    if kind == "zip":
        if not (raw.isdigit() and len(raw) == 5):
            return None, "must be a 5-digit US ZIP code"
        return raw, None
    low, high = spec
    try:
        val = int(raw) if kind == "int" else float(raw)
    except ValueError:
        return None, f"'{raw}' isn't a number"
    # Keep whole numbers as ints so the config stays readable ($42, not $42.0)
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    if not (low <= val <= high):
        return None, f"must be between {low} and {high}"
    return val, None


class CommandHandler:
    def __init__(self, config_dir: Path, db=None):
        self.config_dir = config_dir
        self.db = db          # needed only for !bought / !sold / !stats
        self.paths = {
            "settings": config_dir / "settings.yaml",
            "consoles": config_dir / "consoles.yaml",
        }
        self.changed: set[str] = set()   # which files need committing

    # --- individual commands ----------------------------------------------
    def _cmd_help(self) -> str:
        return (
            "**Deal finder commands**\n"
            "`!scan` — scan every console right now\n"
            "`!search <console>` — search one console, e.g. "
            "`!search dslite`, `!search gba-sp`, `!search 3ds`\n"
            "`!settings` — current settings\n"
            "`!consoles` — consoles, aliases, sell prices, on/off\n"
            "`!set <path> <value>` — change a setting\n"
            "`!enable <console>` / `!disable <console>`\n"
            "`!bought <url> <price>` — log a purchase\n"
            "`!sold <#> <total>` — close it out\n"
            "`!stats` — are the bot's estimates actually right?\n"
            "`!list <console> <price> | <notes>` — post to #for-sale\n"
            "`!customer @user` — grant the Customer role\n\n"
            "Examples:\n"
            "`!set pricing.refurb_cost 42`\n"
            "`!set yield.default 0.8`\n"
            "`!set notify.instant_min_tier great`\n"
            "`!set gb_dmg.sell_price 110`\n"
            "`!disable n2ds`\n\n"
            "_Instant while the listener is running; otherwise applied on the "
            "next hourly scan._"
        )

    def _cmd_settings(self) -> str:
        s = _load(self.paths["settings"])
        p, y, d, n = s["pricing"], s["yield"], s["digest"], s["notify"]
        t = p["profit_tiers"]
        return (
            "**Current settings**\n"
            f"Refurb cost: **${p['refurb_cost']}**/unit\n"
            f"Profit tiers: 🟡 marginal **${t['marginal']}** · "
            f"✅ good **${t['good']}** · 🔥 great **${t['great']}**\n"
            f"Screen repair penalty: **−${p['screen_repair_penalty']}**\n"
            f"Parts credit per dud: **${p['parts_credit_per_dud']}**\n"
            f"Default lot yield: **{y['default']:.0%}**\n"
            f"Digest: every **{d['interval_hours']}h**, top **{d['top_n']}** "
            f"deals, {d['lookback_hours']}h lookback\n"
            f"Instant alerts: **{n['instant_min_tier']}** and above, "
            f"max **{n['instant_max_per_run']}**/run\n"
            f"Auction 'ending soon' window: "
            f"**{s['sources']['ebay']['ending_soon_hours']}h**\n"
            "\nChange any of these with `!set <path> <value>` — `!help` for examples."
        )

    def _cmd_consoles(self) -> str:
        c = _load(self.paths["consoles"])["consoles"]
        s = _load(self.paths["settings"])["pricing"]
        r, t = s["refurb_cost"], s["profit_tiers"]
        lines = ["**Tracked consoles** (max buy @ $25/$40/$50 profit)"]
        for key, cfg in c.items():
            mark = "✅" if cfg.get("enabled", True) else "⛔"
            sell = cfg["sell_price"]
            aliases = ", ".join(cfg.get("aliases", [])) or key
            lines.append(
                f"{mark} `{key}` {cfg['name']} — sell **${sell}** → "
                f"${sell - r - t['marginal']:.0f} / ${sell - r - t['good']:.0f} / "
                f"${sell - r - t['great']:.0f}\n"
                f"　　search as: `{aliases}`")
        lines.append("\n_Search one with_ `!search <name>` _· scan all with_ `!scan`")
        return "\n".join(lines)

    def _cmd_set(self, args: list[str]) -> str:
        if len(args) < 2:
            return "❌ Usage: `!set <path> <value>` — e.g. `!set pricing.refurb_cost 42`"
        path, raw = args[0], args[1]

        # Per-console field?  e.g. "gb_dmg.sell_price 110"
        parts = path.split(".")
        if len(parts) == 2 and parts[1] in CONSOLE_FIELDS:
            doc = _load(self.paths["consoles"])
            key, field = parts
            if key not in doc["consoles"]:
                return f"❌ Unknown console `{key}` — try `!consoles`"
            kind, low, high = CONSOLE_FIELDS[field]
            val, err = _coerce(kind, (low, high), raw)
            if err:
                return f"❌ `{path}` {err}"
            old = doc["consoles"][key][field]
            doc["consoles"][key][field] = val
            _save(self.paths["consoles"], doc)
            self.changed.add("consoles")
            return (f"✅ `{key}.{field}`: **{old}** → **{val}**\n"
                    f"_Applies on the next scan._")

        if path not in SETTABLE:
            return (f"❌ `{path}` isn't settable. Settable paths:\n"
                    + ", ".join(f"`{k}`" for k in SETTABLE)
                    + "\nPlus `<console>.sell_price` (see `!consoles`).")

        file_key, kind, *spec = SETTABLE[path]
        spec = spec[0] if kind == "choice" else tuple(spec)
        val, err = _coerce(kind, spec, raw)
        if err:
            return f"❌ `{path}` {err}"

        doc = _load(self.paths[file_key])
        parts = path.split(".")
        parent = _dig(doc, parts[:-1])
        old = parent[parts[-1]]
        parent[parts[-1]] = val
        _save(self.paths[file_key], doc)
        self.changed.add(file_key)
        return f"✅ `{path}`: **{old}** → **{val}**\n_Applies on the next scan._"

    def _cmd_toggle(self, args: list[str], on: bool) -> str:
        if not args:
            return f"❌ Usage: `!{'enable' if on else 'disable'} <console>` — see `!consoles`"
        key = args[0]
        doc = _load(self.paths["consoles"])
        if key not in doc["consoles"]:
            return f"❌ Unknown console `{key}` — try `!consoles`"
        doc["consoles"][key]["enabled"] = on
        _save(self.paths["consoles"], doc)
        self.changed.add("consoles")
        word = "watching" if on else "ignoring"
        return (f"✅ Now **{word}** {doc['consoles'][key]['name']} (`{key}`)\n"
                f"_Applies on the next scan._")

    # --- purchase tracking -------------------------------------------------
    def _cmd_bought(self, args: list[str]) -> str:
        if not self.db:
            return "❌ Purchase tracking needs the database (run via the bot)."
        if len(args) < 2:
            return ("❌ Usage: `!bought <listing url> <what you paid>`\n"
                    "e.g. `!bought https://www.ebay.com/itm/123456789 114.00`")
        url, raw = args[0], args[1].lstrip("$")
        try:
            paid = float(raw)
        except ValueError:
            return f"❌ `{raw}` isn't a price."
        row = self.db.find_listing(url)
        if not row:
            return ("❌ I don't have that listing on file — it has to be one "
                    "the bot showed you. Paste the eBay link from the alert.")
        pid = self.db.add_purchase(row, paid)
        est = row["est_profit"]
        est_txt = f"predicted ~${est:.0f}/unit" if est is not None else "no estimate on file"
        return (f"📦 Logged purchase **#{pid}** — ${paid:.2f}\n"
                f"_{row['title'][:90]}_\n"
                f"Bot {est_txt}. When you've sold them: "
                f"`!sold {pid} <total received>`")

    def _cmd_sold(self, args: list[str]) -> str:
        if not self.db:
            return "❌ Purchase tracking needs the database (run via the bot)."
        if len(args) < 2:
            return "❌ Usage: `!sold <purchase #> <total received> [units]`"
        try:
            pid = int(args[0].lstrip("#"))
            total = float(args[1].lstrip("$"))
        except ValueError:
            return "❌ Usage: `!sold <purchase #> <total received> [units]`"
        units = None
        if len(args) > 2:
            try:
                units = int(args[2])
            except ValueError:
                pass
        if not self.db.mark_sold(pid, total, units):
            return f"❌ No open purchase **#{pid}** (already sold, or wrong number)."
        p = [x for x in self.db.purchases() if x["id"] == pid][0]
        actual = total - p["paid"]
        est = p["est_total"]
        line = f"💰 Purchase **#{pid}** closed — ${total:.2f} in, ${p['paid']:.2f} out → **${actual:.2f}** profit"
        if est is not None:
            diff = actual - est
            verdict = "better than" if diff > 0 else "under"
            line += f"\nBot predicted ~${est:.0f} — {verdict} estimate by ${abs(diff):.0f}."
        return line + "\n_`!stats` for the running picture._"

    def _cmd_stats(self) -> str:
        if not self.db:
            return "❌ Stats need the database (run via the bot)."
        rows = self.db.purchases()
        if not rows:
            return ("📊 Nothing logged yet.\n"
                    "Log a buy with `!bought <url> <price>`, close it with "
                    "`!sold <#> <total>`. After a few flips this tells you "
                    "whether the bot's estimates match reality.")
        closed = [r for r in rows if r["sold_total"] is not None]
        open_ = [r for r in rows if r["sold_total"] is None]
        spent = sum(r["paid"] for r in rows)
        lines = [f"📊 **{len(rows)}** purchase(s) · ${spent:.2f} spent"]
        if closed:
            got = sum(r["sold_total"] for r in closed)
            paid = sum(r["paid"] for r in closed)
            est = sum(r["est_total"] or 0 for r in closed)
            actual = got - paid
            lines.append(f"**Closed {len(closed)}:** ${got:.2f} in − ${paid:.2f} out "
                         f"= **${actual:.2f}** profit")
            if est:
                ratio = actual / est
                lines.append(f"Bot predicted ${est:.0f} → reality is "
                             f"**{ratio:.0%}** of estimate")
                if ratio < 0.75:
                    lines.append("_Estimates are running hot. Consider a lower "
                                 "`yield.default` or higher `pricing.refurb_cost`._")
                elif ratio > 1.25:
                    lines.append("_Estimates are conservative — you could bid higher._")
            if any(r["units_sold"] for r in closed):
                exp = sum(r["est_good"] or 0 for r in closed)
                got_u = sum(r["units_sold"] or 0 for r in closed)
                if exp:
                    lines.append(f"Units: expected {exp}, actually sold {got_u} "
                                 f"(**{got_u / exp:.0%}** of predicted yield)")
        if open_:
            lines.append(f"**Open {len(open_)}:** "
                         + ", ".join(f"#{r['id']} ${r['paid']:.0f}" for r in open_[:8]))
        return "\n".join(lines)

    # --- shop -------------------------------------------------------------
    def _cmd_list(self, rest: str) -> str:
        """!list <console> <price> | <notes>  ->  posts a card to #for-sale"""
        from .notify import discord as nd
        if "|" in rest:
            head, notes = rest.split("|", 1)
        else:
            head, notes = rest, ""
        parts = head.split()
        if len(parts) < 2:
            return ("❌ Usage: `!list <console> <price> | <notes>`\n"
                    "e.g. `!list gba-sp 145 | AGS-101 backlit, new shell, tested`")
        price_raw = parts[-1].lstrip("$")
        name = " ".join(parts[:-1])
        try:
            price = float(price_raw)
        except ValueError:
            return f"❌ `{price_raw}` isn't a price. Put the price before the `|`."

        consoles = _load(self.paths["consoles"])["consoles"]
        key = resolve_console(name, consoles)
        if not key:
            return f"❌ Don't know console `{name}` — try `!consoles`."
        cfg = consoles[key]

        embed = {
            "title": f"🕹️ {cfg['name']} — ${price:.0f}",
            "description": (notes.strip() or
                            "Fully refurbished, tested and ready to play."),
            "color": 0x2ED573,
            "footer": {"text": "Interested? Open a private ticket in "
                               "#private-support to claim it."},
        }
        if nd.post_to_channel("for-sale", embeds=[embed]):
            return f"✅ Posted **{cfg['name']} — ${price:.0f}** to #for-sale"
        return "❌ Couldn't post to #for-sale — is the channel still there?"

    def _cmd_customer(self, args: list[str]) -> str:
        """!customer @user  /  !customer remove @user"""
        from .notify import discord as nd
        if not args:
            return "❌ Usage: `!customer @user` or `!customer remove @user`"
        remove = args[0].lower() == "remove"
        target = args[1] if remove and len(args) > 1 else args[0]
        uid = "".join(ch for ch in target if ch.isdigit())
        if not uid:
            return "❌ Mention the person, e.g. `!customer @kap`"
        ok, msg = nd.grant_role(uid, "Customer", remove=remove)
        if not ok:
            return f"❌ {msg}"
        return (f"✅ Removed the Customer role from <@{uid}>" if remove
                else f"✅ <@{uid}> is now a **Customer** — they can see the "
                     f"owners area.")

    # --- entry point -------------------------------------------------------
    def handle(self, content: str) -> str | None:
        """Returns a reply string, or None if this isn't a command."""
        text = content.strip()
        if not text.startswith("!"):
            return None
        parts = text[1:].split()
        if not parts:
            return None
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("help", "commands"):
                return self._cmd_help()
            if cmd == "settings":
                return self._cmd_settings()
            if cmd == "consoles":
                return self._cmd_consoles()
            if cmd == "set":
                return self._cmd_set(args)
            if cmd == "enable":
                return self._cmd_toggle(args, True)
            if cmd == "disable":
                return self._cmd_toggle(args, False)
            if cmd == "bought":
                return self._cmd_bought(args)
            if cmd == "sold":
                return self._cmd_sold(args)
            if cmd == "stats":
                return self._cmd_stats()
            if cmd == "list":
                return self._cmd_list(text[1:].split(None, 1)[1]
                                      if len(text.split()) > 1 else "")
            if cmd == "customer":
                return self._cmd_customer(args)
        except Exception as e:                    # noqa: BLE001
            log.error("Command %r failed: %s", text, e)
            return f"❌ `{text}` failed: {e}"
        return None                               # unknown ! command: ignore
