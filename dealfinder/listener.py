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


def push_config(log) -> None:
    """Best-effort: commit and push config so the cloud honours the change."""
    def git(*args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, timeout=120)
    try:
        git("add", "config/settings.yaml", "config/consoles.yaml")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return                                   # nothing changed
        git("commit", "-m", "config update via Discord command")
        git("pull", "--rebase", "-q")
        result = git("push", "-q")
        if result.returncode == 0:
            log.info("Config change pushed to GitHub")
        else:
            log.warning("Config push failed: %s", result.stderr.strip()[:200])
    except Exception as e:
        log.warning("Could not push config change: %s", e)


def main() -> None:
    setup_logging()
    log = logging.getLogger("dealfinder.listener")
    load_dotenv(PROJECT_ROOT / ".env")

    db = Database(PROJECT_ROOT / "data" / "dealfinder.db")
    handler = CommandHandler(PROJECT_ROOT / "config")
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
                    notify_discord.send("🔍 Scanning every console — one moment…")
                    try:
                        # send_now so results come back immediately rather
                        # than waiting for the digest interval
                        run_scan(db, RunOpts(send_now=True), log)
                        notify_discord.send("✅ Scan complete.")
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
                    notify_discord.send(f"🔍 Searching **{parts[1]}** — one moment…")
                    try:
                        notify_discord.send(run_search(db, parts[1], RunOpts(), log))
                    except Exception as e:
                        log.error("Search failed: %s", e)
                        notify_discord.send(f"❌ Search failed: `{e}`")
                    continue

                reply = handler.handle(text)
                if reply:
                    notify_discord.send(reply)
                    if handler.changed:
                        push_config(log)
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
