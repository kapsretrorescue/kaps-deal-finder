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
    "digest.per_family_min":          ("settings", "int", 1, 20),
    "digest.per_family_max":          ("settings", "int", 1, 20),
    "digest.lookback_hours":          ("settings", "int", 1, 168),
    "notify.instant_min_tier":        ("settings", "choice", ["great", "good"]),
    "notify.instant_max_per_run":     ("settings", "int", 1, 25),
    "sources.ebay.ending_soon_hours": ("settings", "int", 1, 48),
    "sources.ebay.max_results_per_search": ("settings", "int", 1, 200),
}
# Per-console: "<console_key>.sell_price"
CONSOLE_FIELDS = {"sell_price": ("num", 1, 2000)}


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
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.paths = {
            "settings": config_dir / "settings.yaml",
            "consoles": config_dir / "consoles.yaml",
        }
        self.changed: set[str] = set()   # which files need committing

    # --- individual commands ----------------------------------------------
    def _cmd_help(self) -> str:
        return (
            "**Deal finder commands**\n"
            "`!settings` — current settings\n"
            "`!consoles` — consoles, sell prices, on/off\n"
            "`!set <path> <value>` — change a setting\n"
            "`!enable <console>` / `!disable <console>`\n\n"
            "Examples:\n"
            "`!set pricing.refurb_cost 42`\n"
            "`!set yield.default 0.8`\n"
            "`!set notify.instant_min_tier great`\n"
            "`!set gb_dmg.sell_price 110`\n"
            "`!disable n2ds`\n\n"
            "_Changes apply on the next hourly scan._"
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
            f"Digest: every **{d['interval_hours']}h**, "
            f"{d['per_family_min']}–{d['per_family_max']} per family, "
            f"{d['lookback_hours']}h lookback\n"
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
            lines.append(
                f"{mark} `{key}` {cfg['name']} — sell **${sell}** → "
                f"${sell - r - t['marginal']:.0f} / ${sell - r - t['good']:.0f} / "
                f"${sell - r - t['great']:.0f}")
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
        except Exception as e:                    # noqa: BLE001
            log.error("Command %r failed: %s", text, e)
            return f"❌ `{text}` failed: {e}"
        return None                               # unknown ! command: ignore
