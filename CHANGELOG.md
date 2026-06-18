# Changelog

## 0.3.1.0 — 2026-06-18

- Gmail: `_get_message` no longer retries on permanent 4xx errors (e.g. 404 for a deleted message). The history API can reference messages that have since been deleted; before this fix, the 404 was retried 3× and then wrapped in `tenacity.RetryError`, which `__main__.py` failed to catch — silently crashing the morning run.
- `__main__.py`: broaden Gmail / GCal fetch error handling from `except RuntimeError` to `except Exception`, so non-`RuntimeError` failures (including `tenacity.RetryError`) still trigger the fail-loud Telegram error message instead of dropping out silently.

## 0.3.0.0 — 2026-05-26

- Briefing rewrite (openclaw-style): single LLM call generates the full Telegram briefing — header, ⚠️ URGENT cross-bucket section, WORK (Gmail → GCal), PERSONAL (Gmail → iCloud), TL;DR. Source attribution is required on every urgent item.
- Removed: `digest.py`, the per-bucket classifier-then-render pipeline, and the `confidence_threshold` plumbing.
- Gmail: history-path filter moved from the `messagesAdded` stub (where `labelIds` is often absent) into `_fetch_message_details` so the UNREAD / CATEGORY_PERSONAL check uses authoritative metadata. Fixes a regression where the history-path silently returned 0 messages.
- launchd: plist paths corrected to the actual install location.
- Fail-loud: any LLM failure now sends a short error message to Telegram and exits nonzero; no fallback renderer.

## 0.2.0.0 — 2026-05-08

- Gmail: history-path fetch now honors `UNREAD` and (when `primary_tab_only`) `CATEGORY_PERSONAL`. Previously only the date-fallback path filtered, so personal counts ballooned after the first run.
- GCal: extend window to today 00:00 KST → tomorrow 08:30 KST so early-morning events surface in the prior digest. Skip working-location events.
- iCloud: same window logic; tomorrow's all-day events are excluded (they'll show in tomorrow's run).
- Initial scaffold: Gmail/GCal/iCloud → Haiku classifier → Telegram digest.
