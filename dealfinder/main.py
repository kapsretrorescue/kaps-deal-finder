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
from .commands import CommandHandler, resolve_console
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
    auctions_only: bool = False  # cheap pass: only auctions nearing their end


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


def process_commands(db: Database, opts: RunOpts, log) -> list[str]:
    """Read and apply Discord !commands.

    Returns the console names from any `!search <console>` requests, so the
    caller can run them (searches need a scan, which lives here, not in the
    command handler).
    """
    if opts.mock:
        return []
    searches: list[str] = []
    try:
        contents, newest = notify_discord.read_commands(db.get_meta("last_command_id"))
        handler = CommandHandler(PROJECT_ROOT / "config", db)
        replies = []
        for content in contents:
            text = content.strip()
            low = text.lower()
            if low.startswith("!scan"):
                continue                        # a scan happens anyway
            if low.startswith("!search"):
                parts = text.split(maxsplit=1)
                searches.append(parts[1] if len(parts) > 1 else "")
                continue
            reply = handler.handle(text)
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
    return searches


def warn(settings: dict, message: str, log) -> None:
    """Push an operational problem to Discord.

    Failures used to end at a log file nobody reads, which made a dead bot
    look exactly like a quiet market.
    """
    log.error(message)
    try:
        if settings.get("notify", {}).get("alert_on_failure", True):
            notify_discord.send(f"⚠️ **Deal finder problem**\n{message[:1500]}")
    except Exception:
        pass          # never let the warning path throw


def check_staleness(db: Database, settings: dict, log) -> None:
    """Tell me if scans stopped happening for a while."""
    limit = settings.get("notify", {}).get("stale_hours", 8)
    last = db.get_meta("last_successful_scan")
    if not last:
        return
    try:
        gap = (datetime.now(timezone.utc)
               - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return
    if gap >= limit:
        warn(settings, f"No successful scan for {gap:.0f} hours — scans may "
                       f"have been failing or not running.", log)


def run_search(db: Database, name: str, opts: RunOpts, log) -> tuple[str, list]:
    """On-demand `!search <console>`: hit eBay for ONE console and return the
    best current listings ranked by estimated profit.

    Unlike the scheduled scan this ignores 'already notified' — you asked to
    see what's out there right now, including listings you've been shown
    before.
    """
    settings, consoles, _ = load_config()
    key = resolve_console(name, consoles)
    if not key:
        options = ", ".join(f"`{a}`" for c in consoles.values()
                            for a in (c.get("aliases") or [])[:1])
        return (f"❌ Don't know console `{name}`. Try: {options}", [])

    cfg = consoles[key]
    log.info("Searching eBay for %s", cfg["name"])
    # Only this console's search terms, but score against the FULL config so
    # exclusions still work (e.g. a GBA SP listing won't count as plain GBA).
    listings = ebay.fetch({key: cfg}, settings)

    matched_ids = []
    for listing in listings:
        result = analysis.analyze(listing, consoles, settings)
        if result.excluded_reason or key not in result.consoles:
            continue
        if db.is_seen(listing.source, listing.listing_id):
            db.refresh_price(result)      # keep auction prices current
        else:
            db.add(result)
        matched_ids.append(listing.listing_id)

    if not matched_ids:
        return digest.embeds_search([], cfg["name"], consoles, settings["pricing"])

    limit = settings["digest"].get("search_results", 10)
    marks = ",".join("?" * len(matched_ids))
    rows = db.conn.execute(
        f"""SELECT * FROM listings WHERE listing_id IN ({marks})
            ORDER BY CASE tier WHEN 'great' THEN 0 WHEN 'good' THEN 1
                               WHEN 'marginal' THEN 2 WHEN 'skip' THEN 3
                               ELSE 4 END,
                     est_profit DESC
            LIMIT ?""",
        (*matched_ids, limit),
    ).fetchall()
    return digest.embeds_search(rows, cfg["name"], consoles, settings["pricing"])


def run_scan(db: Database, opts: RunOpts, log) -> None:
    """Fetch, score, store, alert, and (if due) send the digest."""
    settings, consoles, families = load_config()

    # ---- 1. Fetch ---------------------------------------------------------
    enabled = ["mock"] if opts.mock else [
        k for k, v in settings["sources"].items()
        if v.get("enabled") and k in SOURCE_MODULES]
    check_staleness(db, settings, log)

    listings = []
    for name in enabled:
        try:
            if name == "ebay":
                listings.extend(ebay.fetch(consoles, settings,
                                           auctions_only=opts.auctions_only))
            elif not opts.auctions_only:
                listings.extend(SOURCE_MODULES[name].fetch(consoles, settings))
        except Exception as e:
            # eBay is the source that matters; if it breaks, say so loudly.
            if name == "ebay" and not opts.dry_run:
                warn(settings, f"eBay source failed: {e}", log)
            else:
                log.error("Source %s crashed: %s", name, e)

    # ---- 1b. Enrich the shortlist with full descriptions -------------------
    # Search results carry only a condition label. Anything the seller
    # disclosed in the body text is invisible until we pull the item — and
    # that's exactly where "water damage" tends to live. Only fetch details
    # for listings that look like deals on title alone, to save quota.
    if not opts.mock:
        shortlist = []
        for listing in listings:
            if listing.source != "ebay" or listing.price is None:
                continue
            first = analysis.analyze(listing, consoles, settings)
            if first.consoles and not first.excluded_reason and \
                    first.tier in ("great", "good", "marginal"):
                shortlist.append(listing)
        if shortlist:
            try:
                details = ebay.fetch_details(
                    [l.listing_id for l in shortlist], settings)
                for listing in shortlist:
                    info = details.get(listing.listing_id)
                    if not info:
                        continue
                    listing.description = f"{listing.description} {info['description']}"
                    if info["local_pickup"]:
                        # Confirmed against eBay's own location data, not a
                        # filter — so zeroing the postage is safe here.
                        listing.local_pickup = True
                        listing.shipping = 0.0
                        listing.price_note = f"local pickup — {info['where']}"
            except Exception as e:
                log.warning("Item detail fetch failed: %s", e)

    # ---- 2. Score ---------------------------------------------------------
    new_matches = 0
    dropped_on_detail = 0
    ending_realerts = []
    price_drops: list[tuple[str, str, float]] = []   # source, id, old price
    pd_pct = settings["pricing"].get("price_drop_min_pct", 10)
    pd_usd = settings["pricing"].get("price_drop_min_usd", 10)
    for listing in listings:
        result = analysis.analyze(listing, consoles, settings)
        if result.excluded_reason:
            # A red flag found only in the body text — worth counting, since
            # this is the whole point of pulling descriptions.
            if listing.description and "red flag" in result.excluded_reason:
                dropped_on_detail += 1
            continue
        if not result.consoles:
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
        elif result.tier in ("great", "good") and result.total_cost is not None:
            # Already seen, but the seller cut the price. Without this the
            # listing would stay invisible forever after its first scan.
            was = db.price_dropped(listing.source, listing.listing_id,
                                   result.total_cost, pd_pct, pd_usd)
            if was:
                db.refresh_price(result)
                db.note_alerted_price(listing.source, listing.listing_id,
                                      result.total_cost)
                price_drops.append((listing.source, listing.listing_id, was))
    log.info("%d listings fetched, %d new matches stored, %d ending-soon "
             "re-alerts, %d price drops, %d rejected on description",
             len(listings), new_matches, len(ending_realerts),
             len(price_drops), dropped_on_detail)
    # A run that fetched nothing at all means the source is broken, not that
    # the market went quiet — 900+ listings is the normal figure.
    if listings and not opts.dry_run:
        db.set_meta("last_successful_scan", datetime.now(timezone.utc).isoformat())
    elif not listings and not opts.mock and not opts.dry_run:
        warn(settings, "Scan returned zero listings — the eBay source is "
                       "probably broken (a normal run sees hundreds).", log)

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

    # Price drops jump the queue — a seller who just cut a price is the most
    # time-sensitive thing in the run.
    drop_rows, drop_prices = [], {}
    for source, lid, was in price_drops:
        r = db.conn.execute("SELECT * FROM listings WHERE source=? AND listing_id=?",
                            (source, lid)).fetchone()
        if r and (source, lid) not in seen_keys:
            drop_rows.append(r)
            drop_prices[lid] = was

    cap = settings["notify"].get("instant_max_per_run", 8)
    all_instant = (drop_rows + instant_rows + ending_rows)[:cap]
    overflow = (len(drop_rows) + len(instant_rows) + len(ending_rows)
                - len(all_instant))
    if all_instant:
        head, cards = digest.embeds_instant(all_instant, consoles,
                                            settings["pricing"], drop_prices)
        if overflow > 0:
            head += f" · +{overflow} more in the digest"
        if opts.dry_run:
            print("\n" + "=" * 60 + "\nDRY RUN — instant alert:\n" + "=" * 60)
            print(digest.build_instant(all_instant, consoles, settings["pricing"]))
        elif settings["notify"].get("discord"):
            if notify_discord.send(head, cards):
                db.mark(instant_rows, "notified")
                log.info("Instant alert sent: %d listing(s).", len(all_instant))
            else:
                log.warning("Instant alert failed; will retry next run.")

    # ---- 4. Digest --------------------------------------------------------
    if opts.auctions_only:
        return          # the hourly auction sweep never sends a digest

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
    head, cards = digest.embeds_digest(rows, consoles_with_fams, settings)
    if not cards:
        log.info("Digest empty — nothing ranked.")
        return

    if opts.dry_run:
        print("\n" + "=" * 60 + "\nDRY RUN — digest:\n" + "=" * 60)
        print(digest.build_digest(rows, consoles_with_fams, settings))
        return

    sent = False
    if settings["notify"].get("discord"):
        sent = notify_discord.send(head, cards) or sent
    if settings["notify"].get("email"):
        # email stays plain text
        sent = email_notify.send(
            digest.build_digest(rows, consoles_with_fams, settings)) or sent
    if sent:
        db.set_last_digest()
        log.info("Digest sent.")


def run_one_command(db: Database, text: str, opts: RunOpts, log) -> None:
    """Execute a single command and post the result to Discord.

    Used by the slash-command path: Cloudflare answers Discord instantly,
    then GitHub Actions runs the command here and posts the real reply.
    """
    text = text.strip()
    low = text.lower()
    log.info("Slash command: %s", text[:120])

    if low.startswith("!scan"):
        run_scan(db, RunOpts(send_now=True), log)
        return

    if low.startswith("!search"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            notify_discord.send("❌ Usage: `/search console:<name>`")
            return
        head, cards = run_search(db, parts[1], opts, log)
        notify_discord.send(head, cards)
        return

    reply = CommandHandler(PROJECT_ROOT / "config", db).handle(text)
    notify_discord.send(reply or f"❓ Didn't understand `{text[:80]}`")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kap's Retro Rescue deal finder")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-now", action="store_true")
    parser.add_argument("--auctions-only", action="store_true",
                        help="cheap pass: only auctions nearing their end")
    parser.add_argument("--command", metavar="TEXT",
                        help="run one command (e.g. '!scan') and exit — used "
                             "by the slash-command workflow")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("dealfinder")
    load_dotenv(PROJECT_ROOT / ".env")

    opts = RunOpts(mock=args.mock, dry_run=args.dry_run, send_now=args.send_now,
                   auctions_only=args.auctions_only)
    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")

    try:
        if args.command:
            run_one_command(db, args.command, opts, log)
            return

        if opts.auctions_only:      # quick sweep: no commands, no digest
            run_scan(db, opts, log)
            return

        # Commands first, so a config change applies to this very scan
        searches = process_commands(db, opts, log)
        for name in searches:
            head, cards = run_search(db, name, opts, log)
            if opts.dry_run:
                print(head, f"({len(cards)} cards)")
            else:
                notify_discord.send(head, cards)
        run_scan(db, opts, log)
    except Exception as e:
        # Anything that reaches here would otherwise be a silent death.
        try:
            settings = load_yaml("settings.yaml")
        except Exception:
            settings = {}
        warn(settings, f"Run crashed: {type(e).__name__}: {e}", log)
        raise


if __name__ == "__main__":
    main()
