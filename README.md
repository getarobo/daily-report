Version 0.3.1.0 - 2026-06-18

# daily-report

Personal daily-briefing bot. Pulls **Gmail** (personal + work), **Google Calendar** (×2), and **iCloud Calendar**, classifies importance with **Claude Haiku 4.5**, and posts a 2-section + ⚠️ Action-needed digest to **Telegram** every morning at 08:00 KST via `launchd`.

Single user, single machine, batch-only. Optimized for **recall over precision** — the worst-case morning is missing something urgent.

## Setup (one-time)

> **No Anthropic API key needed.** The classifier subprocesses into `claude -p`, so the daily run bills against your existing Claude Team seat.

### 1. Install

```bash
just install
```

This creates `.venv`, installs deps including the path-dep `openclaw-icalendar-sync` from `~/.openclaw/workspace/skills/icalendar-sync`. Verify:

```bash
.venv/bin/python -c "from icalendar_sync import get_events; print('ok')"
```

### 2. Claude (no API key needed)

The classifier shells out to the local `claude -p` CLI, so the daily run bills against your **Claude Team seat** — no separate Anthropic API key required. Just confirm Claude Code is logged in:

```bash
claude --version           # 2.x or newer
echo "hi" | claude -p --model claude-haiku-4-5 --output-format json | head -1
```

If that prints a JSON envelope ending with `"is_error":false`, you're set. If it errors, run `claude` interactively once to refresh auth.

### 3. Telegram bot

1. Open Telegram, message `@BotFather`, run `/newbot`, save the bot token.
2. Start a chat with your new bot and send any message.
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` to find your `chat.id`.
4. Store the bot token + chat id:

```bash
.venv/bin/python -c "import keyring; keyring.set_password('daily-report', 'telegram-bot', input('bot token: '))"
echo "TELEGRAM_CHAT_ID=<your chat id>" > .env
```

### 4. Gmail + Google Calendar OAuth

You will need a Google Cloud project with **Gmail API** and **Calendar API** enabled, and an **OAuth 2.0 client (Desktop app)** credential. Save the client JSON as `~/.config/daily-report/google-client.json`.

The pinned scopes are read-only:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.readonly`

Run the auth flow once per account:

```bash
just auth-google you@gmail.com
just auth-google you@your-workspace.com
```

Each opens a browser, exchanges the code, and stores the refresh token in Keychain (`daily-report/google:<email>`).

### 5. iCloud Calendar

The `icalendar-sync` skill should already be configured. Verify:

```bash
.venv/bin/python -m icalendar_sync list
```

If not configured, see the skill's setup at `~/.claude/skills/icalendar-sync/`.

### 6. Config

```bash
cp config.example.yaml config.yaml
$EDITOR config.yaml   # fill in account emails + calendar names
```

### 7. First run

```bash
just dry-run            # prints HTML digest to stdout, no send
just dry-run-notify     # also pushes to Telegram
just run                # what launchd will invoke
```

### 8. Schedule via launchd

```bash
cp ops/launchd/com.genehan.daily-report.plist           ~/Library/LaunchAgents/
cp ops/launchd/com.genehan.daily-report.healthcheck.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.genehan.daily-report.plist
launchctl load ~/Library/LaunchAgents/com.genehan.daily-report.healthcheck.plist
```

The main job fires at 08:00 KST; the healthcheck at 08:10 pings Telegram if the main job did not advance the state file.

## How it works

```
launchd @ 08:00  ->  daily_report run
                       ├── fetchers/gmail.py    (per account; historyId -> after:date fallback)
                       ├── fetchers/gcal.py     (today + tomorrow)
                       └── fetchers/icloud.py   (icalendar_sync.get_events, library import)
                       -> classifier.py        (Haiku 4.5, prompt-cached system prompt;
                                                per-bucket confidence thresholds)
                       -> digest.py            (HTML, sectioned per bucket + Action-needed)
                       -> notify/telegram.py   (split per section if any single message > 3500 chars)
                       -> state.json           (~/Library/Application Support/daily-report/)

launchd @ 08:10  ->  daily_report healthcheck  (Telegram ping if state stale)
```

## Locked design decisions

- **In-digest only.** No real-time worker. Items needing action get ⚠️ inline at 08:00.
- **Recall over precision.** `never_flag` stays narrow; classifier leans inclusive.
- **Per-bucket calibration.** `personal` confidence threshold 0.4, `work` 0.6 (Workspace has no Primary tab → noisier feed).
- **All secrets in Keychain.** OAuth tokens, Anthropic API key, Telegram bot token. `.env` only stores `TELEGRAM_CHAT_ID` (non-secret routing).
- **Read-only OAuth scopes.** A token leak can read mail/calendar but cannot send/modify/delete.
- **Failure semantics.** Gmail/GCal failures abort the run with a Telegram error ping. iCloud failures degrade gracefully (digest emits `(iCloud calendar unavailable)`).

See `.claude/plans/` (or the canonical plan at `~/.claude/plans/ok-new-project-this-wise-unicorn.md`) for the full ADR and the deep-interview transcript that produced these decisions.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: icalendar_sync` after `just install` | path dep moved | re-check the path in `pyproject.toml`, or re-install the skill |
| `RefreshError` from Google | refresh token revoked | `just auth-google <email>` again |
| Digest empty when you expect mail | state-file watermark already advanced past today | delete `~/Library/Application Support/daily-report/state.json` and re-run |
| 08:10 healthcheck pings every day | main job is not running | `launchctl list \| grep daily-report` and check `~/Library/Logs/daily-report.log` |
| Action-needed section is too noisy on work side | classifier threshold too low for the volume | raise `importance.work.confidence_threshold` in `config.yaml` |
