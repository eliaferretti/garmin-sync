// Telegram -> GitHub Actions bridge, deployed as a Cloudflare Worker.
//
// Telegram pushes every message to this Worker, which checks it really came
// from Telegram and really came from you, then fires a repository_dispatch
// that starts the sync workflow. Setup instructions are in bot/README.md.
//
// No secrets live in this file: all five values come from the Worker's
// environment, which is why it is safe to keep in a public repo.

// Mirrors LOOKBACK in garmin_sync.py, which clamps to the same ceiling.
const MAX_RESEND = 20;

const HELP = [
  "Commands:",
  "/sendlast - re-send your most recent activity",
  "/sendlast 3 - re-send the last 3 activities",
  "/sync - send anything new that has not been sent yet",
  "/help - this message",
].join("\n");

async function sendMessage(env, chatId, text) {
  const response = await fetch(
    `https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    }
  );
  // A revoked or mistyped BOT_TOKEN fails right here, and the only symptom
  // is a bot that never answers. Say so in the log.
  if (!response.ok) {
    console.log(
      `sendMessage failed: ${response.status} ${await response.text()}`
    );
  }
  return response.ok;
}

async function dispatch(env, resend) {
  const response = await fetch(
    `https://api.github.com/repos/${String(env.GITHUB_REPO).trim()}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        // GitHub rejects API calls that do not identify themselves.
        "User-Agent": "garmin-sync-bot",
      },
      body: JSON.stringify({
        event_type: "telegram-command",
        client_payload: { resend },
      }),
    }
  );
  return response;
}

export default {
  async fetch(request, env) {
    // Telegram only ever POSTs. A GET is you checking the deploy worked, so
    // report which settings exist. Booleans only: never echo a secret.
    if (request.method !== "POST") {
      // Browsers request this straight after the health check and it just
      // clutters the log with a second entry.
      if (new URL(request.url).pathname === "/favicon.ico") {
        return new Response(null, { status: 204 });
      }
      const configured = {
        BOT_TOKEN: Boolean(env.BOT_TOKEN),
        CHAT_ID: Boolean(env.CHAT_ID),
        GITHUB_TOKEN: Boolean(env.GITHUB_TOKEN),
        GITHUB_REPO: env.GITHUB_REPO || null,
        WEBHOOK_SECRET: Boolean(env.WEBHOOK_SECRET),
      };
      return new Response(
        JSON.stringify({ bot: "garmin-sync", configured }, null, 2),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // Telegram echoes back the secret registered with setWebhook. Without
    // this, anyone who learned the URL could drive the bot.
    const token = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.WEBHOOK_SECRET || token !== String(env.WEBHOOK_SECRET).trim()) {
      console.log(
        env.WEBHOOK_SECRET
          ? "403: secret_token sent by Telegram does not match WEBHOOK_SECRET"
          : "403: WEBHOOK_SECRET is not set on this Worker"
      );
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      console.log("ignored: body was not JSON");
      return new Response("ok");
    }

    // Dashboard paste often carries a trailing space or newline, and an
    // untrimmed compare then rejects every message with no visible cause.
    const expectedChat = String(env.CHAT_ID || "").trim();

    const message = update.message || update.edited_message;
    const text = message && message.text ? message.text.trim() : "";
    const chatId = message && message.chat ? message.chat.id : null;

    // Everything below answers 200 even when it refuses: on a non-2xx
    // Telegram redelivers the same update over and over for hours.
    if (!text) {
      console.log("ignored: update carried no message text");
      return new Response("ok");
    }
    if (String(chatId).trim() !== expectedChat) {
      // By far the most common setup mistake, and previously invisible.
      console.log(
        `ignored: message came from chat ${chatId}, ` +
          `but CHAT_ID is set to "${expectedChat}". ` +
          `If you want this chat, set CHAT_ID to ${chatId} and redeploy.`
      );
      return new Response("ok");
    }

    const parts = text.split(/\s+/);
    // "/sendlast@MyBot 3" -> "/sendlast"
    const command = parts[0].toLowerCase().split("@")[0];
    const argument = parts[1];

    if (command === "/start" || command === "/help") {
      await sendMessage(env, chatId, HELP);
      return new Response("ok");
    }

    if (command !== "/sendlast" && command !== "/sync") {
      await sendMessage(env, chatId, `Unknown command ${command}.\n\n${HELP}`);
      return new Response("ok");
    }

    // An empty resend means a normal catch-up sync.
    let resend = "";
    if (command === "/sendlast") {
      const count = argument === undefined ? 1 : Number(argument);
      if (!Number.isInteger(count) || count < 1 || count > MAX_RESEND) {
        await sendMessage(
          env,
          chatId,
          `Give a whole number between 1 and ${MAX_RESEND}, for example: /sendlast 3`
        );
        return new Response("ok");
      }
      resend = String(count);
    }

    console.log(`${command} from ${chatId} -> dispatch resend="${resend}"`);
    const response = await dispatch(env, resend);
    console.log(`GitHub dispatch returned ${response.status}`);

    if (response.ok) {
      await sendMessage(
        env,
        chatId,
        resend
          ? `Queued: re-sending your last ${resend} activity(ies).`
          : "Queued: checking for new activities."
      );
    } else {
      // 401/403 here is almost always an expired GITHUB_TOKEN.
      await sendMessage(
        env,
        chatId,
        `Could not start the sync: GitHub returned ${response.status}.`
      );
    }

    return new Response("ok");
  },
};
