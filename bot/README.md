# Telegram command bot

Lets you type `/sendlast` to your existing bot and get the activity back in
about a minute, instead of waiting for the next scheduled run.

```
you ──/sendlast──▶ Telegram ──▶ Cloudflare Worker ──▶ GitHub Actions ──▶ CSV back to you
                               (bot/worker.js)        (repository_dispatch)
```

The Worker exists only because Telegram cannot call GitHub Actions directly:
it needs a URL to push each message to. It runs on Cloudflare's free tier
(100k requests/day; this uses a handful) and costs nothing.

## Commands

| Command | Effect |
| --- | --- |
| `/sendlast` | Re-send your most recent activity, even if it was already sent |
| `/sendlast 3` | Re-send the last 3 activities (max 20) |
| `/sync` | Send anything new that has not been sent yet |
| `/help` | List the commands |

Only messages from your own `CHAT_ID` are accepted. Anyone else who finds the
bot is ignored silently.

## Setup

All five steps work from a phone browser. Nothing to install.

### 1. Create a GitHub token

github.com → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token.

- Repository access: **Only select repositories** → `garmin-sync`
- Permissions → Repository permissions → **Contents: Read and write**
- Expiration: pick a long one, and note the date — see Troubleshooting

Copy the token. It is shown only once.

### 2. Make up a webhook secret

Any random string, e.g. from a password generator. Used to prove that
requests to the Worker really come from Telegram. Call it `WEBHOOK_SECRET`.

### 3. Deploy the Worker

dash.cloudflare.com → Workers & Pages → Create → Start with Hello World →
Deploy. Then **Edit code**, replace everything with the contents of
[`worker.js`](worker.js), and Deploy again.

Note the Worker URL: `https://<name>.<subdomain>.workers.dev`

### 4. Add the Worker's secrets

Worker → Settings → Variables and Secrets. Add all five as **Secret**
(not plaintext):

| Name | Value |
| --- | --- |
| `BOT_TOKEN` | Same bot token the workflow uses |
| `CHAT_ID` | Same chat ID the workflow uses |
| `GITHUB_TOKEN` | The token from step 1 |
| `GITHUB_REPO` | `eliaferretti/garmin-sync` |
| `WEBHOOK_SECRET` | The string from step 2 |

Deploy once more so the new values take effect.

### 5. Point Telegram at the Worker

Open this in any browser, with your values filled in:

```
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<WEBHOOK_SECRET>
```

You should see `{"ok":true,...,"description":"Webhook was set"}`.

Now message `/sendlast` to your bot.

### Optional: autocomplete

In BotFather, `/setcommands` on your bot, then paste:

```
sendlast - Re-send your most recent activity
sync - Send anything new
help - List commands
```

## Troubleshooting

Check the Worker's live logs first: Cloudflare dashboard → your Worker →
Logs. Every Telegram message shows up there.

| Symptom | Cause |
| --- | --- |
| Bot never replies | Webhook not registered. Visit `https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo` — `url` should be your Worker, and `last_error_message` tells you what Telegram hit |
| "GitHub returned 401" | `GITHUB_TOKEN` expired or was revoked. Redo step 1 and update the secret |
| "GitHub returned 403" | Token is missing **Contents: Read and write**, or was not scoped to this repo |
| "GitHub returned 404" | `GITHUB_REPO` is wrong, or the token cannot see the repo |
| Bot replies "Queued" but nothing arrives | The workflow ran and failed — check the Actions tab. Usually an expired `GARMINTOKENS` |

Two things worth knowing:

- `repository_dispatch` always runs the workflow from the **default branch**.
  The trigger has to be merged to `main` before the bot can work at all.
- Registering a webhook disables `getUpdates` for this bot. Undo with
  `https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook`.
