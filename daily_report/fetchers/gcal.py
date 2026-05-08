"""Google Calendar fetcher — today + early-morning tomorrow (until next 08:30 run).

Skips working-location events.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_exponential

from daily_report.auth import load_credentials
from daily_report.config import AccountConfig, CalendarConfig

log = logging.getLogger(__name__)


def fetch_gcal(
    calendars: list[CalendarConfig],
    accounts: list[AccountConfig],
) -> dict[str, list[dict[str, Any]]]:
    """Fetch today+tomorrow events for each gcal calendar.

    Returns mapping of ``calendar_id -> list of event dicts``.
    Each event dict has: ``id, summary, dtstart, dtend, location, description, bucket``.
    """
    account_by_id = {a.id: a for a in accounts}
    results: dict[str, list[dict[str, Any]]] = {}

    for cal in calendars:
        if cal.type != "gcal":
            continue
        if not cal.account_ref:
            log.warning("Calendar %s has no account_ref, skipping", cal.id)
            continue
        account = account_by_id.get(cal.account_ref)
        if not account:
            log.warning("account_ref %r not found for calendar %s", cal.account_ref, cal.id)
            continue
        try:
            events = _fetch_calendar_events(account, cal)
            results[cal.id] = events
        except RuntimeError as exc:
            raise RuntimeError(
                f"GCal fetch failed for calendar {cal.id} ({account.email}): {exc}"
            ) from exc

    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_calendar_events(
    account: AccountConfig,
    cal: CalendarConfig,
) -> list[dict[str, Any]]:
    """Fetch events from 'primary' calendar for *account* in today+tomorrow window."""
    creds = load_credentials(account.email)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Window: today 00:00 KST → tomorrow 08:30 KST (right before the next run).
    # Catches early-morning next-day events (e.g., 7am dentist) one digest early.
    kst = ZoneInfo("Asia/Seoul")
    today_kst = datetime.now(kst).date()
    time_min = datetime.combine(today_kst, time.min, tzinfo=kst)
    time_max = datetime.combine(today_kst + timedelta(days=1), time(8, 30), tzinfo=kst)

    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )

    events = []
    for item in response.get("items", []):
        # Skip GCal "Where you're working from" pseudo-events.
        if item.get("eventType") == "workingLocation":
            continue
        start = item.get("start", {})
        end = item.get("end", {})
        events.append(
            {
                "id": item.get("id", ""),
                "summary": item.get("summary", "(no title)"),
                "dtstart": start.get("dateTime") or start.get("date", ""),
                "dtend": end.get("dateTime") or end.get("date", ""),
                "location": item.get("location", ""),
                "description": item.get("description", ""),
                "bucket": cal.bucket,
                "calendar_id": cal.id,
            }
        )

    log.info(
        "Fetched %d events from GCal calendar %s (%s)",
        len(events),
        cal.id,
        account.email,
    )
    return events
