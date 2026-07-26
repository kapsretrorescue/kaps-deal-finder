"""One run of the deal finder: fetch -> score -> dedupe -> alert -> digest.

Tier logic (see analysis.py):
  🔥 great / ✅ good  -> instant Discord alert (notify.instant_min_tier)
  🟡 marginal         -> hourly family digest only
  ❌ skip             -> stored for dedup, never shown
Auctions only alert inside their final window (their price is just the
current bid until then), and only once.

Usage:
  python -m dealfinder.main               # one scheduled run
  python -m dealfinder.main --mock        # fake listings, no API keys needed
  python -m dealfinder.main --dry-run     # print alerts/digest, send nothing
  python -m dealfinder.main --send-now    # ignore the digest interval
  python -m dealfinder.listener           # stay running, answer commands live
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import analysis, digest
from .commands import CommandHandler
from .db import Database
from .notify import discord as notify_discord
from .notify import email_notify
from .sources import ebay, mock, reddit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_MODULES = {"ebay": ebay, "reddit": reddit, "mock": mock}


@dataclass
class RunOpts:
    """What a single scan should do."""
    mock: bool = False
    dry_run: bool = False
    send_now: bool = False       # ignore the digest interval


def setup_logging() -> None:
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)
    (PROJECT_ROOT / "data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(PROJECT_ROOT / "logs" / "dealfinder.log", encoding="utf-8"),
        ],
    )


def load_yaml(name: str) -> dict:
    with open(PROJECT_ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config() -> tuple[dict, dict, dict]:
    """(settings, consoles, families) — re-read every run so config edits
    made by Discord commands take effect immediately."""
    settings = load_yaml("settings.yaml")
    doc = load_yaml("consoles.yaml")
    return settings, doc["consoles"], doc["families"]


def process_commands(db: Database, opts: RunOpts, log) -> bool:
    """Read and apply any Discord !commands. Returns True if a scan was
    explicitly requested with !scan."""
    if opts.mock:
        return False
    scan_requested = False
    try:
        contents, newest = notify_discord.read_commands(db.get_meta("last_command_id"))
        handler = CommandHandler(PROJECT_ROOT / "config")
        replies = []
        for content in contents:
            if content.strip().lower().startswith("!scan"):
                scan_requested = True
                continue
            reply = handler.handle(content)
            if reply:
                replies.append(reply)
        if newest:
            db.set_meta("last_command_id", newest)
        if replies:
            log.info("Handled %d Discord command(s)", len(replies))
            if opts.dry_run:
                print("\n".join(replies))
            else:
                notify_discord.send("\n\n".join(replies))
    except Exception as e:
        log.error("Command handling failed: %s", e)
    return scan_requested


def run_scan(db: Database, opts: RunOpts, log) -> None:
    """Fetch, score, store, alert, and (if due) send the digest."""
    settings, consoles, families = load_config()

    # ---- 1. Fetch ---------------------------------------------------------
    enabled = ["mock"] if opts.mock else [
        k for k, v in settings["sources"].items()
        if v.get("enabled") and k in SOURCE_MODULES]
    listings = []
    for name in enabled:
        try:
            listings.extend(SOURCE_MODULES[name].fetch(consoles, settings))
        except Exception as e:
            log.error("Source %s crashed: %s", name, e)

    # ---- 2. Score ---------------------------------------------------------
    new_matches = 0
    ending_realerts = []
    for listing in listings:
        result = analysis.analyze(listing, consoles, settings)
        if result.excluded_reason or not result.consoles:
            continue
        if not db.is_seen(listing.source, listing.listing_id):
            db.add(result)
            new_matches += 1
        elif listing.ending_soon and result.tier in ("great", "good"):
            row = db.conn.execute(
                "SELECT ending_notified FROM listings WHERE source=? AND listing_id=?",
                (listing.source, listing.listing_id)).fetchone()
            if row and not row["ending_notified"]:
                db.refresh_price(result)
                ending_realerts.append((listing.source, listing.listing_id))
    log.info("%d listings fetched, %d new matches stored, %d ending-soon re-alerts",
             len(listings), new_matches, len(ending_realerts))

    # ---- 3. Instant alerts ------------------------------------------------
    def _alertable_now(row) -> bool:
        """Auctions with time left don't alert — their price is only the
        current bid. They wait for the final window."""
        if row["listing_type"] != "AUCTION" or not row["end_time"]:
            return True
        try:
            end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
        except ValueError:
            return True
        window = settings["sources"]["ebay"].get("ending_soon_hours", 2)
        return (end - datetime.now(timezone.utc)).total_seconds() <= window * 3600

    min_tier = settings["notify"].get("instant_min_tier", "good")
    instant_rows = [r for r in db.pending_instant_rows(min_tier) if _alertable_now(r)]
    seen_keys = {(r["source"], r["listing_id"]) for r in instant_rows}
    ending_rows = []
    for key in ending_realerts:
        if key not in seen_keys:
            r = db.conn.execute(
                "SELECT * FROM listings WHERE source=? AND listing_id=?", key).fetchone()
            if r:
                ending_rows.append(r)
    if ending_rows:
        db.mark(ending_rows, "ending_notified")

    cap = settings["notify"].get("instant_max_per_run", 8)
    all_instant = (instant_rows + ending_rows)[:cap]
    overflow = len(instant_rows) + len(ending_rows) - len(all_instant)
    if all_instant:
        alert = digest.build_instant(all_instant, consoles, settings["pricing"])
        if overflow > 0:
            alert += f"\n\n…plus {overflow} more — see the digest."
        if opts.dry_run:
            print("\n" + "=" * 60 + "\nDRY RUN — instant alert:\n" + "=" * 60)
            print(alert)
        elif settings["notify"].get("discord"):
            if notify_discord.send(alert):
                db.mark(instant_rows, "notified")
                log.info("Instant alert sent: %d listing(s).", len(all_instant))
            else:
                log.warning("Instant alert failed; will retry next run.")

    # ---- 4. Digest --------------------------------------------------------
    if not (opts.send_now or opts.dry_run):
        last = db.last_digest()
        if last is not None:
            hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours < settings["digest"]["interval_hours"]:
                log.info("Digest not due yet (%.1fh since last).", hours)
                return

    rows = db.digest_rows(settings["digest"]["lookback_hours"])
    if not rows:
        log.info("Nothing to put in a digest.")
        return
    consoles_with_fams = dict(consoles)
    consoles_with_fams["_families"] = families
    text = digest.build_digest(rows, consoles_with_fams, settings)
    if not text.strip():
        log.info("Digest empty after family grouping.")
        return

    if opts.dry_run:
        print("\n" + "=" * 60 + "\nDRY RUN — digest:\n" + "=" * 60)
        print(text)
        return

    sent = False
    if settings["notify"].get("discord"):
        sent = notify_discord.send(text) or sent
    if settings["notify"].get("email"):
        sent = email_notify.send(text) or sent
    if sent:
        db.set_last_digest()
        log.info("Digest sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kap's Retro Rescue deal finder")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-now", action="store_true")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("dealfinder")
    load_dotenv(PROJECT_ROOT / ".env")

    opts = RunOpts(mock=args.mock, dry_run=args.dry_run, send_now=args.send_now)
    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")

    # Commands first, so a config change applies to this very scan
    process_commands(db, opts, log)
    run_scan(db, opts, log)


if __name__ == "__main__":
    main()
