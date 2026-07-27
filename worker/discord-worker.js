/**
 * Kap's Retro Rescue — Discord slash-command receiver.
 *
 * Runs on Cloudflare's free tier, always warm, so commands answer instantly
 * whether or not any PC is switched on.
 *
 * Flow:
 *   Discord  --POST-->  this Worker  --repository_dispatch-->  GitHub Actions
 *      ^                    |                                       |
 *      |                    +-- instant "on it" reply               |
 *      +-------------------------- results posted via webhook ------+
 *
 * Discord demands a reply within 3 seconds, which is far too short to scan
 * eBay. So the Worker acknowledges immediately and the real work happens in
 * Actions, which posts the results to the channel when it finishes (~1 min).
 *
 * Secrets this Worker needs (set them in the Cloudflare dashboard, never in
 * this file):
 *   DISCORD_PUBLIC_KEY  - from the Discord developer portal, General Information
 *   GITHUB_TOKEN        - fine-grained PAT with Actions: read & write
 *   GITHUB_REPO         - kapsretrorescue/kaps-deal-finder
 */

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;
const CHANNEL_MESSAGE = 4;

/** Discord signs every request; unsigned traffic must be rejected with 401. */
async function verify(request, body, publicKey) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!signature || !timestamp) return false;

  const hex = (s) => Uint8Array.from(s.match(/.{1,2}/g).map((b) => parseInt(b, 16)));
  try {
    const key = await crypto.subtle.importKey(
      "raw", hex(publicKey), { name: "Ed25519", namedCurve: "Ed25519" },
      false, ["verify"]
    );
    return await crypto.subtle.verify(
      { name: "Ed25519" }, key, hex(signature),
      new TextEncoder().encode(timestamp + body)
    );
  } catch (err) {
    return false;
  }
}

function reply(content) {
  return new Response(
    JSON.stringify({ type: CHANNEL_MESSAGE, data: { content } }),
    { headers: { "Content-Type": "application/json" } }
  );
}

/** Turn a slash command into the same text command the bot already knows. */
function toTextCommand(data) {
  const opt = (name) => (data.options || []).find((o) => o.name === name)?.value;
  switch (data.name) {
    case "scan":     return { text: "!scan", note: "🔍 Scanning every console — results in about a minute." };
    case "search":   return { text: `!search ${opt("console")}`, note: `🔍 Searching **${opt("console")}** — results in about a minute.` };
    case "settings": return { text: "!settings", note: "⚙️ Fetching your settings…" };
    case "consoles": return { text: "!consoles", note: "🎮 Fetching your console list…" };
    case "stats":    return { text: "!stats", note: "📊 Crunching your purchase history…" };
    case "help":     return { text: "!help", note: "📖 Fetching the command list…" };
    case "set":      return { text: `!set ${opt("path")} ${opt("value")}`, note: `⚙️ Setting \`${opt("path")}\` to \`${opt("value")}\`…` };
    case "list": {
      const notes = opt("notes") ? ` | ${opt("notes")}` : "";
      return { text: `!list ${opt("console")} ${opt("price")}${notes}`, note: "🕹️ Posting it to #for-sale…" };
    }
    default: return null;
  }
}

/** Ask GitHub Actions to actually run the command. */
async function dispatch(env, commandText, channelId) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "kaps-deal-finder-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      event_type: "command",
      client_payload: { command: commandText, channel_id: channelId },
    }),
  });
  if (!res.ok) console.log("dispatch failed", res.status, await res.text());
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Kap's Retro Rescue command endpoint", { status: 200 });
    }
    const body = await request.text();

    if (!(await verify(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("bad signature", { status: 401 });
    }

    const interaction = JSON.parse(body);

    // Discord health-checks the endpoint before it will accept it.
    if (interaction.type === PING) {
      return new Response(JSON.stringify({ type: PONG }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    if (interaction.type === APPLICATION_COMMAND) {
      const mapped = toTextCommand(interaction.data);
      if (!mapped) return reply("❓ I don't know that command.");
      // Fire the dispatch after responding, so Discord's 3s deadline is met.
      ctx.waitUntil(dispatch(env, mapped.text, interaction.channel_id));
      return reply(mapped.note);
    }

    return new Response("unhandled", { status: 400 });
  },
};
