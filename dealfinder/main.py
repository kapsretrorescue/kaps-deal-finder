"""One run of the deal finder: fetch -> analyze -> dedupe -> maybe digest.

Meant to be run on a schedule (e.g. hourly via Windows Task Scheduler).
A digest only goes out when digest.interval_hours has elapsed AND there's
something new to report — so hourly runs won't spam you.

Usage:
  python -m dealfinder.main               # normal scheduled run
  python -m dealfinder.main --mock        # use fake listings (no API keys needed)
  python -m dealfinder.main --dry-run     # print digest to console, send nothing
  python -m dealfinder.main --send-now    # ignore the interval, send if anything is pending
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import analysis, digest
from .db import Database
from .notify import discord as notify_discord
from .notify import email_notify
from .sources import ebay, mock, reddit

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SOURCE_MODULES = {"ebay": ebay, "reddit": reddit, "mock": mock}


def setup_logging() -> None:
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)  # logs/ is gitignored, so it
    (PROJECT_ROOT / "data").mkdir(exist_ok=True)  # may not exist on a fresh clone
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Kap's Retro Rescue deal finder")
    parser.add_argument("--mock", action="store_true",
                        help="use only the mock source (testing without API keys)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest instead of sending; nothing is marked notified")
    parser.add_argument("--send-now", action="store_true",
                        help="send pending digest immediately, ignoring interval_hours")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("dealfinder")
    load_dotenv(PROJECT_ROOT / ".env")

    settings = load_yaml("settings.yaml")
    consoles = load_yaml("consoles.yaml")["consoles"]
    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")

    # ---- 1. Fetch from every enabled source -------------------------------
    if args.mock:
        enabled = ["mock"]
    else:
        enabled = [k for k, v in settings["sources"].items()
                   if v.get("enabled") and k in SOURCE_MODULES]
    listings = []
    for name in enabled:
        try:
            listings.extend(SOURCE_MODULES[name].fetch(consoles, settings))
        except Exception as e:  # one broken source shouldn't kill the run
            log.error("Source %s crashed: %s", name, e)

    # ---- 2. Analyze new listings, store everything we matched -------------
    new_matches = 0
    for listing in listings:
        if db.is_seen(listing.source, listing.listing_id):
            continue
        result = analysis.analyze(listing, consoles, settings)
        if not result.consoles:
            continue  # not one of our consoles
        db.add(result)
        new_matches += 1
    log.info("%d listings fetched, %d new matches stored", len(listings), new_matches)

    # ---- 3. Instant deal alerts -------------------------------------------
    # Listings that beat the max-buy price go out immediately (Discord only);
    # everything else waits for the digest below.
    if settings["notify"].get("instant_deals"):
        deal_rows = db.pending_deal_rows()
        if deal_rows:
            alert = digest.build_instant(deal_rows, consoles)
            if args.dry_run:
                print("\n" + "=" * 60 + "\nDRY RUN — instant alert that WOULD be sent:\n" + "=" * 60)
                print(alert)
            elif settings["notify"].get("discord"):
                if notify_discord.send(alert):
                    db.mark_notified(deal_rows)
                    log.info("Instant alert sent: %d deal(s).", len(deal_rows))
                else:
                    log.warning("Instant alert failed; deals stay pending for the digest.")

    # ---- 4. Digest gate ---------------------------------------------------
    pending = db.pending_digest_rows()
    if not pending:
        log.info("Nothing pending for digest.")
        return

    if not (args.send_now or args.dry_run):
        last = db.last_digest()
        interval = settings["digest"]["interval_hours"]
        if last is not None:
            hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_since < interval:
                log.info("%d item(s) banked; next digest in %.1f h",
                         len(pending), interval - hours_since)
                return

    text = digest.build(pending, consoles)

    if args.dry_run:
        print("\n" + "=" * 60 + "\nDRY RUN — digest that WOULD be sent:\n" + "=" * 60)
        print(text)
        return

    # ---- 5. Send digest ---------------------------------------------------
    sent = False
    if settings["notify"].get("discord"):
        sent = notify_discord.send(text) or sent
    if settings["notify"].get("email"):
        sent = email_notify.send(text) or sent

    if sent:
        db.mark_notified(pending)
        db.set_last_digest()
        log.info("Digest sent: %d item(s).", len(pending))
    else:
        log.warning("No notifier succeeded; items stay pending for next run.")


if __name__ == "__main__":
    main()
