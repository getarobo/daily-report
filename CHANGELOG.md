# Changelog

## 0.2.0.0 — 2026-05-08

- Gmail: history-path fetch now honors `UNREAD` and (when `primary_tab_only`) `CATEGORY_PERSONAL`. Previously only the date-fallback path filtered, so personal counts ballooned after the first run.
- GCal: extend window to today 00:00 KST → tomorrow 08:30 KST so early-morning events surface in the prior digest. Skip working-location events.
- iCloud: same window logic; tomorrow's all-day events are excluded (they'll show in tomorrow's run).
- Initial scaffold: Gmail/GCal/iCloud → Haiku classifier → Telegram digest.
