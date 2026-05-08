"""iCloud Calendar fetcher — uses icalendar_sync library import.

Window: today (full day) plus tomorrow's timed events that start before 08:30
KST — matches the cron schedule so users see early-morning events one digest
ahead. Tomorrow's all-day events are excluded; they'll show in tomorrow's run.
"""

from __future__ import annotations

import contextlib
import io
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from daily_report.config import CalendarConfig

log = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_NEXT_RUN_HOUR = 8
_NEXT_RUN_MINUTE = 30


def fetch_icloud(calendars: list[CalendarConfig]) -> dict[str, list[dict[str, Any]]]:
    """Fetch today+tomorrow events from iCloud calendars.

    Returns mapping of ``calendar_id -> list of event dicts``.
    Each event dict has: ``summary, dtstart, dtend, uid, location, description, bucket``.

    The underlying library prints to stdout while fetching; we suppress that
    so dry-run output stays clean.

    If CalDAV is unavailable, logs a warning and returns an empty result
    (iCloud is best-effort per plan §failure-modes).
    """
    results: dict[str, list[dict[str, Any]]] = {}

    for cal in calendars:
        if cal.type != "icloud":
            continue
        if not cal.calendar_names:
            log.warning("iCloud calendar %s has no calendar_names, skipping", cal.id)
            continue
        events = _fetch_icloud_calendar(cal)
        results[cal.id] = events

    return results


def _fetch_icloud_calendar(cal: CalendarConfig) -> list[dict[str, Any]]:
    """Fetch events from all named iCloud calendars for *cal*."""
    try:
        from icalendar_sync import get_events  # type: ignore[import]
    except ImportError:
        log.warning("icalendar_sync not installed — iCloud calendar %s unavailable", cal.id)
        return []

    today_kst = datetime.now(_KST).date()
    tomorrow_kst = today_kst + timedelta(days=1)
    cutoff = datetime.combine(tomorrow_kst, time(_NEXT_RUN_HOUR, _NEXT_RUN_MINUTE), tzinfo=_KST)

    all_events: list[dict[str, Any]] = []
    for calendar_name in cal.calendar_names:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                raw_events = get_events(calendar_name, days_ahead=2)
            kept = [evt for evt in raw_events if _in_window(evt, today_kst, cutoff)]
            for evt in kept:
                evt["bucket"] = cal.bucket
                evt["calendar_id"] = cal.id
            all_events.extend(kept)
            log.info(
                "Fetched %d events from iCloud calendar %r (after window filter; %d raw)",
                len(kept),
                calendar_name,
                len(raw_events),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("iCloud calendar %r unavailable: %s — skipping", calendar_name, exc)

    return all_events


def _in_window(evt: dict[str, Any], today: date, cutoff: datetime) -> bool:
    """Keep events that fall in [today 00:00 KST, cutoff). All-day tomorrow excluded."""
    dtstart = evt.get("dtstart")
    if isinstance(dtstart, datetime):
        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=_KST)
        return dtstart.astimezone(_KST) < cutoff
    if isinstance(dtstart, date):
        # All-day events: keep only if on today (drop tomorrow's all-day).
        return dtstart == today
    return False
