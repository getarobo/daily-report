# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

`just` recipes are the source of truth (`justfile`):

- `just install` — create `.venv` (Python ≥ 3.11) and `pip install -e '.[dev]'`. Pulls the path-dep `openclaw-icalendar-sync` from `~/.openclaw/workspace/skills/icalendar-sync` — if that path is missing, install will fail.
- `just dry-run` — full pipeline, prints HTML to stdout, no Telegram send.
- `just dry-run-notify` — dry-run **and** push to Telegram (end-to-end smoke).
- `just run` — what launchd invokes.
- `just healthcheck` — currently a stub (`daily_report/healthcheck.py`, Phase 4).
- `just auth-google <email>` — interactive OAuth; refresh token lands in macOS Keychain.
- `just lint` / `just fmt` — `ruff check .` + `ruff format --check .` / `ruff format .`.
- `just test` — `pytest -q`. Note: `pyproject.toml` declares `testpaths = ["tests"]` but no `tests/` directory exists yet; add tests under `tests/` to avoid collection errors.

Single-test invocation: `.venv/bin/pytest -q tests/path/to/test.py::test_name`.

CLI entry point is `python -m daily_report` (subcommands: `run [--dry-run] [--notify]`, `healthcheck`, `auth-google <email>`).

## Architecture

The pipeline is linear and runs once per launchd fire:

```
launchd → daily_report run
  fetchers/{gmail,gcal,icloud}.py  → classifier.py  → digest.py  → notify/telegram.py
                                                                  → state.json
```

`__main__.py:_cmd_run` orchestrates the whole flow; everything else is a pure-ish module called from there.

### Key cross-file concepts

**Buckets (`personal` / `work`)** are the primary partitioning axis and flow end-to-end:
- Set on each `AccountConfig` / `CalendarConfig` in `config.yaml`.
- Fetchers stamp `item["bucket"]` onto every email/event dict.
- `classifier.classify_items(items, bucket, cfg)` reads `cfg.importance.<bucket>` for `confidence_threshold` + `always_flag` / `never_flag` lists. Personal threshold is intentionally **lower** (0.4) than work (0.6) because Gmail's Primary tab pre-filters noise but Workspace has no Primary tab.
- `digest.render_digest` re-splits items by bucket into Personal/Work sections.

**Item dict shape (informal contract).** Emails carry `subject`, `sender`, `snippet`, `date`, `bucket`, `account_id`. Events carry `summary`, `dtstart`, `dtend`, `location`, `description`, `bucket`, `calendar_id`. The classifier overlays `flag`, `confidence`, and either `summary` (emails) or `note` (events — preserves the calendar title in `summary`). `digest._is_email` distinguishes the two by checking for `sender` / absence of `dtstart`.

**Classifier subprocesses to `claude -p`, not the Anthropic SDK.** This is intentional — billing flows through the user's Claude Team seat instead of an API key. `classifier._call_classifier` shells out with `--system-prompt` (cached server-side), `--output-format json`, `--no-session-persistence`, `--max-budget-usd 1`, and `DISABLE_OMC=1`. Returns parse failures or CLI absence are caught by `_fallback_classify` (no flagging, raw subjects). When changing the prompt or output format, preserve the JSON-array contract — `_call_classifier` enforces `len(parsed) == len(items)`.

**Failure semantics differ per source.** Gmail and GCal failures **abort** the run via `RuntimeError` and trigger `notify.telegram.send_error`. iCloud is **best-effort** — `fetchers/icloud.py` swallows exceptions, logs a warning, and returns `[]` so the digest still ships. Don't change this asymmetry without revisiting the design notes.

**Time windows.**
- Gmail: incremental via `historyId` watermark stored in `state.json` per account; falls back to `after:<yesterday> is:unread` (and `category:primary` for accounts with `primary_tab_only=true`) if the historyId 404s. The history-path filter must match the date-path filter (`UNREAD` + `CATEGORY_PERSONAL`); a regression where it didn't was the 0.2.0.0 fix.
- GCal & iCloud: window is `today 00:00 KST → tomorrow 08:30 KST`. The 08:30 cutoff matches the launchd schedule so early-morning next-day events surface in the *prior* digest. Tomorrow's all-day events are excluded (they appear in tomorrow's run). Working-location pseudo-events are skipped.

### Secrets, state, and config locations

- **Keychain** (`keyring`, service = `daily-report`):
  - `google:<email>` — JSON blob with refresh token + client metadata (per-account).
  - The bot token key is whatever `cfg.telegram.bot_token_keyring` says (default `daily-report/telegram-bot`).
- **`.env`** — only `TELEGRAM_CHAT_ID` (non-secret routing). Loaded by `__main__._load_dotenv`.
- **`~/.config/daily-report/google-client.json`** — OAuth client secret (downloaded from Google Cloud Console, Desktop-app credential). Read-only scopes: `gmail.readonly`, `calendar.readonly`.
- **State**: `~/Library/Application Support/daily-report/state.json` (atomic write via `state.save_state`). Holds per-account Gmail `history_id` and `last_run_ts`. Deleting it resets the Gmail watermark to the date-fallback path.
- **Config search order** (`config.load_config`): explicit path → `./config.yaml` → `~/.config/daily-report/config.yaml`. Schema is mirrored in pydantic models in `daily_report/config.py`; the source of truth for fields is `config.example.yaml`.

### launchd

`ops/launchd/com.genehan.daily-report.plist` fires Mon–Fri 08:30 KST and Sat–Sun 10:00 KST (despite the README still saying 08:00). The healthcheck plist runs ~10 min later and pings Telegram if `state.last_run_ts` is stale. The plist's `WorkingDirectory` and `ProgramArguments` hard-code `/Users/genehan/projects/genehome/agents/daily/daily-report/` — if the repo is checked out elsewhere, the plist won't find `.venv`. Update both paths when relocating.

### Telegram output

`digest.render_digest` returns a **list** of HTML strings, splitting at 3500 chars per section. `notify.telegram.send_messages` sends them sequentially with `parse_mode="HTML"`. Subjects/senders/locations are HTML-escaped; only specific tags (`<b>`, `<i>`) are emitted. The `httpx` logger is silenced in `__main__._setup_logging` because it logs the full bot-token URL at INFO.

## Project conventions

- **Python ≥ 3.11**, `ruff` lint+format, line length 100, double quotes, `select = ["E","F","I","B","UP","SIM"]`, `E501` ignored.
- **Recall over precision** for the classifier: when in doubt, flag. `never_flag` lists stay narrow; the personal `confidence_threshold` is deliberately permissive.
- **Read-only OAuth scopes** are a hard requirement — a leaked token must not be able to send/modify mail or calendar events.
- Design ADR lives at `~/.claude/plans/ok-new-project-this-wise-unicorn.md` (also referenced from README). Read it before changing failure-mode behavior, scope policy, or the in-digest-only decision (no real-time worker).
