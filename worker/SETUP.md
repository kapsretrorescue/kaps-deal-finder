# Always-on slash commands — setup

Everything on the code side is done and pushed. These are the four steps
only you can do (they involve accounts and secrets).

Total time: about 15 minutes. Cost: $0.

---

## 1. Create a GitHub token (3 min)

The Worker needs permission to start a workflow run.

1. Go to **github.com/settings/personal-access-tokens/new**
2. Token name: `discord-worker`
3. Expiration: **No expiration** (or set a reminder to rotate it)
4. Repository access: **Only select repositories** → `kaps-deal-finder`
5. Permissions → Repository permissions → **Contents: Read and write**
6. **Generate token** and copy it — GitHub shows it once

---

## 2. Get your Discord public key (1 min)

1. **discord.com/developers/applications** → *Kap's Deal Finder*
2. **General Information** → copy the **Public Key**

(This is not a secret in the password sense — it only lets the Worker verify
that requests genuinely came from Discord.)

---

## 3. Deploy the Cloudflare Worker (7 min)

1. Sign up free at **dash.cloudflare.com** (no card needed)
2. **Compute (Workers)** → **Create** → **Start with Hello World!** → name it
   `kaps-commands` → **Deploy**
3. Click **Edit code**, delete everything in the editor, and paste the whole
   contents of `worker/discord-worker.js` from this repo → **Deploy**
4. Go to the Worker's **Settings** → **Variables and Secrets** → add three,
   each **type: Secret**:

   | Name | Value |
   |---|---|
   | `DISCORD_PUBLIC_KEY` | the public key from step 2 |
   | `GITHUB_TOKEN` | the token from step 1 |
   | `GITHUB_REPO` | `kapsretrorescue/kaps-deal-finder` |

5. **Deploy** again so the secrets take effect
6. Copy the Worker URL — it looks like
   `https://kaps-commands.<your-subdomain>.workers.dev`

---

## 4. Point Discord at the Worker (1 min)

1. Back in **discord.com/developers/applications** → *Kap's Deal Finder*
2. **General Information** → **Interactions Endpoint URL**
3. Paste the Worker URL → **Save Changes**

Discord immediately sends a test ping. If it saves without complaint, it
worked. If it says the endpoint could not be verified, the public key in
step 4 doesn't match — recopy it.

---

## Done — try it

In Discord, type `/` and you'll see:

| Command | What it does |
|---|---|
| `/scan` | Scan every console now |
| `/search console:dslite` | Search one console |
| `/settings` | Current pricing and alert settings |
| `/consoles` | Tracked consoles and max buy prices |
| `/set path:pricing.refurb_cost value:42` | Change a setting |
| `/list console:gba-sp price:145 notes:...` | Post to #for-sale |
| `/stats` | Estimate accuracy vs reality |
| `/help` | Everything |

You get an instant "on it" reply, then the real result about a minute later
when GitHub Actions finishes. **This works with every one of your machines
switched off.**

---

## Notes

- The old `!` commands still work whenever `start_listener.bat` is running
  on your PC — that path is genuinely instant end to end. Slash commands are
  the always-on fallback, so keep both.
- Cloudflare's free tier allows 100,000 requests a day. You will use a
  handful.
- If a command silently does nothing, check the Actions tab of the repo —
  the workflow posts a Discord warning when a command fails, but a failure
  to *start* would only show there.
