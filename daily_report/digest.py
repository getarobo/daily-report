"""HTML digest renderer for Telegram.

Two sections: Personal | Work. Each section shows flagged emails first
(unflagged ones are summarized as a "+ N more emails" tail), then events.
Urgent items get a ⚠️ prefix inline. Split per section if it exceeds 3500 chars.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

_MAX_SECTION_CHARS = 3500


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_digest(
    emails: dict[str, list[dict[str, Any]]],
    gcal_events: dict[str, list[dict[str, Any]]],
    icloud_events: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Render all data into a list of HTML message strings for Telegram.

    Returns a list of one or more HTML strings (split per section when needed).
    Each string is safe to send with ``parse_mode="HTML"``.
    """
    personal_items: list[dict[str, Any]] = []
    work_items: list[dict[str, Any]] = []

    for msgs in emails.values():
        for msg in msgs:
            (work_items if msg.get("bucket") == "work" else personal_items).append(msg)
    for evts in gcal_events.values():
        for evt in evts:
            (work_items if evt.get("bucket") == "work" else personal_items).append(evt)
    for evts in icloud_events.values():
        personal_items.extend(evts)

    sections: list[str] = []
    if personal_items:
        sections.append(_render_bucket("Personal", "👤", personal_items))
    if work_items:
        sections.append(_render_bucket("Work", "💼", work_items))

    if not sections:
        return ["<b>Daily Digest</b>\n\nNo new items."]

    # Header with date
    date_str = datetime.now().strftime("%A, %B %-d")
    header = f"<b>Daily Digest — {html.escape(date_str)}</b>\n\n"

    # Split sections that exceed the limit
    messages: list[str] = []
    for i, section in enumerate(sections):
        full = (header if i == 0 else "") + section
        if len(full) <= _MAX_SECTION_CHARS:
            messages.append(full)
        else:
            # Section itself too long — chunk it
            chunks = _split_section(section, _MAX_SECTION_CHARS)
            for j, chunk in enumerate(chunks):
                prefix = header if i == 0 and j == 0 else ""
                messages.append(prefix + chunk)

    return messages


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_bucket(label: str, emoji: str, items: list[dict[str, Any]]) -> str:
    lines = [f"<b>{html.escape(emoji + ' ' + label)}</b>"]

    emails = [i for i in items if _is_email(i)]
    events = [i for i in items if not _is_email(i)]

    flagged_emails = [m for m in emails if m.get("flag")]
    other_email_count = len(emails) - len(flagged_emails)

    for msg in flagged_emails:
        lines.append(_format_email(msg))
    if other_email_count:
        lines.append(f"<i>+ {other_email_count} more email{'s' if other_email_count != 1 else ''}</i>")

    events.sort(key=_event_sort_key)
    for evt in events:
        lines.append(_format_event(evt))

    return "\n".join(lines)


def _event_sort_key(evt: dict[str, Any]) -> tuple[int, str]:
    dt = evt.get("dtstart")
    if isinstance(dt, datetime):
        return (0, dt.isoformat())
    if isinstance(dt, date):
        return (0, dt.isoformat())
    if isinstance(dt, str):
        return (0, dt)
    return (1, "")


# ---------------------------------------------------------------------------
# Item formatters
# ---------------------------------------------------------------------------


def _is_email(item: dict[str, Any]) -> bool:
    return "sender" in item or ("subject" in item and "dtstart" not in item)


def _bullet(flagged: bool) -> str:
    return "⚠️ " if flagged else "• "


_RE_PREFIX = re.compile(r"^(re|fwd?|회신|답장):\s*", re.IGNORECASE)


def _clean_subject(subject: str) -> str:
    prev = None
    while subject != prev:
        prev = subject
        subject = _RE_PREFIX.sub("", subject)
    return subject.strip() or "(no subject)"


def _sender_name(sender: str) -> str:
    # 'Display Name <user@example.com>' -> 'Display Name'
    # "noreply@apple.com"                     ->  "noreply@apple.com"
    if "<" in sender:
        name = sender.split("<", 1)[0].strip().strip('"').strip()
        return name or sender.split("<", 1)[1].rstrip(">").strip()
    return sender.strip()


def _format_email(item: dict[str, Any]) -> str:
    subject = html.escape(_clean_subject(item.get("subject") or ""))
    sender = html.escape(_sender_name(item.get("sender") or ""))
    line = f"{_bullet(bool(item.get('flag')))}<b>{subject}</b>"
    if sender:
        line += f" — <i>{sender}</i>"
    return line


def _format_event(item: dict[str, Any]) -> str:
    title = html.escape(item.get("summary") or "(no title)")
    dtstart = item.get("dtstart") or ""
    location = html.escape(item.get("location") or "")
    note = html.escape(item.get("note") or "")

    time_str = _format_time(dtstart)
    parts = [f"{_bullet(bool(item.get('flag')))}<b>{title}</b>"]
    if time_str:
        parts.append(f"  {html.escape(time_str)}")
    if location:
        parts.append(f"  📍 {location}")
    if note:
        parts.append(f"  <i>{note}</i>")
    return "\n".join(parts)


def _format_time(dtstart: Any) -> str:
    if not dtstart:
        return ""
    # GCal returns ISO strings ("2024-01-15T09:00:00+09:00" or "2024-01-15");
    # iCloud (CalDAV) returns datetime/date objects.
    if isinstance(dtstart, datetime):
        return dtstart.strftime("%-I:%M %p")
    if isinstance(dtstart, date):
        return dtstart.strftime("%b %-d (all day)")
    if isinstance(dtstart, str):
        try:
            if "T" in dtstart:
                return datetime.fromisoformat(dtstart).strftime("%-I:%M %p")
            return datetime.strptime(dtstart, "%Y-%m-%d").strftime("%b %-d (all day)")
        except ValueError:
            return dtstart
    return str(dtstart)


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------


def _split_section(section: str, max_chars: int) -> list[str]:
    """Split a section's text into chunks of at most *max_chars* characters."""
    if len(section) <= max_chars:
        return [section]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in section.split("\n"):
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks or [section[:max_chars]]
