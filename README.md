# Kap's Retro Rescue — Deal Finder

Monitors eBay and Reddit r/hardwareswap for cheap, repairable handheld
consoles, works out per-unit prices for lots, labels "for parts" vs
"tested working" language, and sends a Discord digest (default: at most
one every 3 hours).

## How it works

```
Task Scheduler (hourly)
  └─ run_dealfinder.bat
       └─ python -m dealfinder.main
            1. fetch listings from every enabled source (sources/*.py)
            2. match against your consoles (config/consoles.yaml)
            3. parse lot quantity, classify condition, compute per-unit price
            4. compare to your max-buy thresholds (computed from resale/repair)
            5. store in SQLite (data/dealfinder.db) — dedup, nothing repeats
            6. if 3h have passed AND something is new → Discord digest
```

The max-buy math: `max_buy = resale × (1 − fee_rate) − repair_cost − min_profit`
(working listings use the smaller `refurb_cost_working` instead of `repair_cost`).
All numbers live in `config/` — **the resale/repair values are placeholders;
edit them to match your real numbers.**

## Setup checklist

Already done: virtual environment, dependencies, hourly scheduled task
("Kaps Deal Finder" — runs while you're logged in).

Still yours to do (each source/notifier silently skips itself until its
key exists, so do these in any order):

1. **Copy `.env.example` to `.env`** and fill in keys as you get them.
2. **Discord webhook (~30 sec):** your server → Server Settings →
   Integrations → Webhooks → New Webhook → pick a channel → Copy URL →
   paste as `DISCORD_WEBHOOK_URL`. Install the Discord phone app and
   allow notifications for that channel = free "text message" alerts.
3. **eBay keys (~10 min):** register free at https://developer.ebay.com →
   create an application → use the **Production** keyset → App ID goes in
   `EBAY_CLIENT_ID`, Cert ID in `EBAY_CLIENT_SECRET`.
4. **Reddit keys (~5 min):** logged in, visit https://www.reddit.com/prefs/apps →
   "create another app" → type **script** → redirect uri `http://localhost` →
   the string under the app name is `REDDIT_CLIENT_ID`, the secret is
   `REDDIT_CLIENT_SECRET`.

### Test commands (run from this folder)

```
.venv\Scripts\python.exe -m dealfinder.main --mock --dry-run   # fake data, prints digest
.venv\Scripts\python.exe -m dealfinder.main --dry-run          # real APIs, prints digest
.venv\Scripts\python.exe -m dealfinder.main --send-now         # real run, send immediately
```

Tip: after a `--mock` test, delete `data/dealfinder.db` so the fake
listings don't linger in your history.

## Facebook Marketplace, Craigslist, Mercari — do this manually

These have no public APIs and scraping them risks account bans (Facebook)
or IP blocks (Craigslist). Their built-in alerts are free and instant:

- **Facebook Marketplace:** search e.g. "game boy broken", set your radius →
  tap the bell / "Save search" → you get app notifications for new matches.
- **Craigslist:** run a search → "save search" (needs a free CL account) →
  enable email alerts.
- **Mercari:** search → tap "Save" on the search → turn on notifications
  in the app.

Set up one saved search per console family (~10 minutes total) and the
apps do the monitoring for you.

## Everyday tweaks (no code)

- **Prices/thresholds:** edit `config/consoles.yaml` (resale, repair_cost)
  and `config/settings.yaml` (fee_rate, min_profit, digest interval).
- **Add a console:** copy any block in `consoles.yaml`, adjust keywords.
- **Digest frequency:** `digest.interval_hours` in `settings.yaml`.
- **Turn a source off:** `enabled: false` in `settings.yaml`.

## Maintenance

- Logs: `logs/dealfinder.log` (app) and `logs/scheduler.log` (scheduled runs).
- Pause the bot: Task Scheduler → "Kaps Deal Finder" → Disable
  (or `schtasks /Change /TN "Kaps Deal Finder" /DISABLE`).
- Remove entirely: `schtasks /Delete /TN "Kaps Deal Finder" /F` and delete
  this folder.
- The scheduled task only runs while you're logged in; no alerts while the
  PC is off. If that starts costing you deals, this folder moves as-is to
  a ~$5/mo VPS or Raspberry Pi with a cron line:
  `0 * * * * cd /path/to/kaps-deal-finder && .venv/bin/python -m dealfinder.main`

## Later ideas

- **SMS:** add a `notify/sms.py` using Twilio (~$1.15/mo + per-message) —
  the notifier interface is one `send(text)` function.
- **Admin dashboard:** POST each deal row to a Supabase table via its REST
  API from `main.py` — no coupling to your site's codebase.
- **More subreddits:** add `"GameSale"` etc. under `sources.reddit.subreddits`.
