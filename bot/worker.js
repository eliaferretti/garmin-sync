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
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

async function dispatch(env, resend) {
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`,
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
    // Telegram only ever POSTs. A GET is someone poking the URL.
    if (request.method !== "POST") {
      return new Response("ok");
    }

    // Telegram echoes back the secret registered with setWebhook. Without
    // this, anyone who learned the URL could drive the bot.
    const token = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.WEBHOOK_SECRET || token !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }

    const message = update.message || update.edited_message;
    const text = message && message.text ? message.text.trim() : "";
    const chatId = message && message.chat ? message.chat.id : null;

    // Everything below answers 200 even when it refuses: on a non-2xx
    // Telegram redelivers the same update over and over for hours.
    if (!text || String(chatId) !== String(env.CHAT_ID)) {
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

    const response = await dispatch(env, resend);

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
