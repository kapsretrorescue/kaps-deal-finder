"""One run of the deal finder: fetch -> score -> dedupe -> alert -> digest.

Tier logic (see analysis.py):
  🔥 great / ✅ good  -> instant Discord alert (configurable via
                         notify.instant_min_tier)
  🟡 marginal         -> hourly family digest only
  ❌ skip             -> stored for dedup, never shown
Auctions already seen get RE-alerted once when they enter their final
window (default 2h) if their current bid still makes them a deal.

Usage:
  python -m dealfinder.main               # normal scheduled run
  python -m dealfinder.main --mock        # fake listings, no API keys needed
  python -m dealfinder.main --dry-run     # print alerts/digest, send nothing
  python -m dealfinder.main --send-now    # ignore digest interval
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Kap's Retro Rescue deal finder")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-now", action="store_true")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("dealfinder")
    load_dotenv(PROJECT_ROOT / ".env")

    settings = load_yaml("settings.yaml")
    consoles_doc = load_yaml("consoles.yaml")
    consoles = consoles_doc["consoles"]
    families = consoles_doc["families"]
    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")

    # ---- 1. Fetch ---------------------------------------------------------
    enabled = ["mock"] if args.mock else [
        k for k, v in settings["sources"].items()
        if v.get("enabled") and k in SOURCE_MODULES]
    listings = []
    for name in enabled:
        try:
            listings.extend(SOURCE_MODULES[name].fetch(consoles, settings))
        except Exception as e:
            log.error("Source %s crashed: %s", name, e)

    # ---- 2. Score every listing ------------------------------------------
    new_matches = 0
    ending_realerts = []   # (source, listing_id) of auctions to re-alert
    for listing in listings:
        result = analysis.analyze(listing, consoles, settings)
        if result.excluded_reason or not result.consoles:
            continue
        if not db.is_seen(listing.source, listing.listing_id):
            db.add(result)
            new_matches += 1
        elif listing.ending_soon and result.tier in ("great", "good"):
            # Seen before, but it's an auction in its final window and the
            # CURRENT bid still clears the profit bar -> refresh + re-alert
            row = db.conn.execute(
                "SELECT ending_notified FROM listings WHERE source=? AND listing_id=?",
                (listing.source, listing.listing_id)).fetchone()
            if row and not row["ending_notified"]:
                db.refresh_price(result)
                ending_realerts.append((listing.source, listing.listing_id))
    log.info("%d listings fetched, %d new matches stored, %d ending-soon re-alerts",
             len(listings), new_matches, len(ending_realerts))

    # ---- 3. Instant alerts ------------------------------------------------
    # Auctions with time left don't instant-alert: their "price" is just the
    # current bid and will climb. They live in the digest as watch items and
    # get ONE instant alert if still a deal inside the final window.
    def _alertable_now(row) -> bool:
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
                "SELECT * FROM listings WHERE source=? AND listing_id=?", key
            ).fetchone()
            if r:
                ending_rows.append(r)
    # ending_notified=1 so each auction only gets one final-window ping
    if ending_rows:
        db.mark(ending_rows, "ending_notified")

    cap = settings["notify"].get("instant_max_per_run", 8)
    all_instant = (instant_rows + ending_rows)[:cap]
    overflow = len(instant_rows) + len(ending_rows) - len(all_instant)
    if all_instant:
        alert = digest.build_instant(all_instant, consoles, settings["pricing"])
        if overflow > 0:
            alert += f"\n\n…plus {overflow} more — see the hourly digest."
        if args.dry_run:
            print("\n" + "=" * 60 + "\nDRY RUN — instant alert:\n" + "=" * 60)
            print(alert)
        elif settings["notify"].get("discord"):
            if notify_discord.send(alert):
                db.mark(instant_rows, "notified")
                log.info("Instant alert sent: %d listing(s).", len(all_instant))
            else:
                log.warning("Instant alert failed; will retry next run.")

    # ---- 4. Hourly digest -------------------------------------------------
    if not (args.send_now or args.dry_run):
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

    if args.dry_run:
        print("\n" + "=" * 60 + "\nDRY RUN — hourly digest:\n" + "=" * 60)
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


if __name__ == "__main__":
    main()
