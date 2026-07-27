"""Live Discord listener — instant replies to !commands and !scan.

Run it and leave it running:   start_listener.bat

It checks Discord every few seconds, so commands answer in seconds instead
of waiting for the next hourly cloud run. The cloud keeps doing the 24/7
scanning; this only handles commands (and on-demand scans).

If this isn't running — PC off, laptop closed — nothing is lost: the next
cloud run picks up whatever you typed, just slower.

Config changes made here are pushed to GitHub automatically, because the
cloud reads its config from the repo. Without the push, a !set would only
apply on this machine.
"""
from __future__ import annotations

import logging
import subprocess
import time

from dotenv import load_dotenv

from .commands import CommandHandler
from .db import Database
from .main import PROJECT_ROOT, RunOpts, run_scan, run_search, setup_logging
from .notify import discord as notify_discord

POLL_SECONDS = 5          # how often to check Discord for new messages
IDLE_SCAN_MINUTES = 0     # 0 = never auto-scan here (the cloud does that)


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, timeout=120)


def pull_latest(log) -> None:
    """Take the cloud's database before doing local work.

    The cloud commits its database after every run. Pulling first means the
    listener shares the cloud's memory of what you've already been shown,
    instead of keeping a divergent copy that re-alerts old listings.
    On conflict the cloud wins — it runs far more often.
    """
    try:
        if _git("pull", "--rebase", "-q").returncode == 0:
            return
        # Conflict — almost always the binary database, which can't be
        # merged. Abort cleanly and take ONLY the cloud's database file.
        # Never reset the working tree: that would throw away uncommitted
        # local work (it once wiped a batch of new scripts).
        _git("rebase", "--abort")
        _git("fetch", "origin", "-q")
        if _git("checkout", "origin/main", "--", "data/dealfinder.db").returncode == 0:
            log.info("Database conflicted; took the cloud's copy.")
        else:
            log.warning("Sync conflict left unresolved — running on local state.")
    except Exception as e:
        log.warning("Could not sync from GitHub: %s", e)


def push_state(log, message: str = "sync from local listener") -> None:
    """Push config and database so the cloud sees local changes."""
    try:
        _git("add", "config/settings.yaml", "config/consoles.yaml",
             "data/dealfinder.db")
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return                                   # nothing changed
        _git("commit", "-m", message)
        _git("pull", "--rebase", "-q")
        result = _git("push", "-q")
        if result.returncode == 0:
            log.info("Pushed local state to GitHub")
        else:
            log.warning("Push failed (cloud will resync next run): %s",
                        result.stderr.strip()[:200])
    except Exception as e:
        log.warning("Could not push: %s", e)


def main() -> None:
    setup_logging()
    log = logging.getLogger("dealfinder.listener")
    load_dotenv(PROJECT_ROOT / ".env")

    pull_latest(logging.getLogger("dealfinder.listener"))
    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")
    handler = CommandHandler(PROJECT_ROOT / "config", db)
    opts = RunOpts()

    notify_discord.send(
        "🟢 **Listener online** — commands answer instantly now.\n"
        "Try `!help`, `!settings`, or `!scan`. "
        "_(When this is off, commands still work on the next hourly run.)_")
    log.info("Listening for Discord commands every %ds. Ctrl+C to stop.", POLL_SECONDS)

    while True:
        try:
            contents, newest = notify_discord.read_commands(
                db.get_meta("last_command_id"))
            if newest:
                db.set_meta("last_command_id", newest)

            for content in contents:
                text = content.strip()
                if not text.startswith("!"):
                    continue
                log.info("Command: %s", text[:80])

                low = text.lower()
                if low.startswith("!scan"):
                    try:
                        pull_latest(log)   # share the cloud's dedup memory
                        # send_now so results come back immediately rather
                        # than waiting for the digest interval
                        run_scan(db, RunOpts(send_now=True), log)
                        push_state(log, "scan state from local listener")
                    except Exception as e:
                        log.error("Scan failed: %s", e)
                        notify_discord.send(f"❌ Scan failed: `{e}`")
                    continue

                if low.startswith("!search"):
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        notify_discord.send(
                            "❌ Usage: `!search <console>` — e.g. `!search dslite`. "
                            "`!consoles` lists the names.")
                        continue
                    try:
                        head, cards = run_search(db, parts[1], RunOpts(), log)
                        notify_discord.send(head, cards)
                    except Exception as e:
                        log.error("Search failed: %s", e)
                        notify_discord.send(f"❌ Search failed: `{e}`")
                    continue

                reply = handler.handle(text)
                if reply:
                    notify_discord.send(reply)
                    # config edits and purchase logs both need to reach the cloud
                    if handler.changed or low.startswith(("!bought", "!sold")):
                        push_state(log, "config/purchase update via Discord")
                        handler.changed.clear()

            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log.info("Listener stopped.")
            notify_discord.send("🔴 **Listener offline** — commands will be "
                                "picked up on the next hourly run.")
            return
        except Exception as e:
            log.error("Listener loop error: %s", e)
            time.sleep(POLL_SECONDS * 4)   # back off, then keep going


if __name__ == "__main__":
    main()
