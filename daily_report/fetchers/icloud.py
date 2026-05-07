"""iCloud Calendar fetcher — uses icalendar_sync library import."""

from __future__ import annotations

import contextlib
import io
import logging
from typing import Any

from daily_report.config import CalendarConfig

log = logging.getLogger(__name__)


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

    all_events: list[dict[str, Any]] = []
    for calendar_name in cal.calendar_names:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                raw_events = get_events(calendar_name, days_ahead=1)
            for evt in raw_events:
                evt["bucket"] = cal.bucket
                evt["calendar_id"] = cal.id
            all_events.extend(raw_events)
            log.info("Fetched %d events from iCloud calendar %r", len(raw_events), calendar_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("iCloud calendar %r unavailable: %s — skipping", calendar_name, exc)

    return all_events
