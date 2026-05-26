"""Briefing generator — calls Claude (Haiku 4.5 by default) via the Claude Code CLI.

Why subprocess to ``claude -p`` instead of the Anthropic SDK:
the user is on a Claude Team subscription and does not have a separate
Anthropic API key. ``claude -p`` uses the locally-logged-in Claude Code
session, billing against the user's Team seat.

Single call: takes the day's fetched emails + calendar events and returns
the final Telegram-HTML briefing as one string. No per-item JSON contract;
Python is no longer in the rendering business.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tenacity import retry, stop_after_attempt, wait_exponential

from daily_report.config import AppConfig

log = logging.getLogger(__name__)

_CLI_TIMEOUT_SEC = 180
_TELEGRAM_MAX_CHARS = 4000  # hard limit is 4096; leave headroom for any wrapper text
_KST = ZoneInfo("Asia/Seoul")

_HINTS_SLOT = "__BUCKET_HINTS__"

_SYSTEM_PROMPT = f"""\
You are the user's morning briefing assistant. You receive raw email and
calendar data and write a single, scannable briefing the user reads on their
phone over coffee. Delivered as a Telegram HTML message.

## Voice
Direct, opinionated, no hedging. You read the inbox FOR them — skim noise
into one-line groupings, spell out anything needing a decision. Match the
source language: Korean subject → Korean summary. Never translate.

## Output structure (omit any section that has no content)

1. Header line: <b>☀️ MORNING BRIEF — Weekday, Month Day, Year</b>
   Use the DATE provided in the input.
2. <b>⚠️ URGENT</b> — items needing action TODAY, regardless of bucket.
   For each urgent item, write:
     • a bold one-line summary,
     • a short paragraph of context that ALWAYS names the source —
       which mailbox (thefightingbee@gmail.com / gene@smtown.com) for
       emails, or which calendar (Google Calendar work / iCloud) for
       events. The user must never have to guess where an item came from.
     • → <b>Action needed:</b> one sentence.
3. <b>💼 WORK (gene@smtown.com)</b>
   - Gmail first. WITHIN the Gmail block, ORDER MATTERS:
     (a) Actionable but not-urgent-enough-for-the-top items go FIRST,
         one per line, each with a one-line summary of what's needed.
     (b) THEN group the remaining noise into one-line categories like
         "<b>Promos:</b> A, B, C", "<b>Newsletters:</b> X, Y",
         "<b>Notifications:</b> ...".
   - Then "<b>Google Calendar:</b>" followed by today's events, or
     "No events today."
4. <b>📬 PERSONAL (thefightingbee@gmail.com)</b>
   - Gmail first, same (a) actionable → (b) grouped noise order.
   - Then "<b>iCloud Calendar:</b>" followed by today's events, or
     "No events today."
5. <b>TL;DR:</b> one or two sentences naming the day's single most important
   thing — or "Nothing urgent today."

## Urgent = today-decision-required
Reply from a human asking a question, calendar invite for today, subject
mentioning "deadline / today / EOD / urgent / now", account suspension,
billing failure, security alert from a real service.

## Never urgent
Newsletters, digests, promos, receipts, shipping/order confirmations,
GitHub / Jira / Slack / CI / monitoring noise, LinkedIn notifications.

{_HINTS_SLOT}

## Formatting
Telegram HTML only — use ONLY <b>, <i>, <code>. Do not use any other tag.
Separate top-level sections with a line containing just "---". Output ONLY
the briefing HTML — no preamble, no closing remarks, no markdown code fences.
"""


def _build_system_prompt(cfg: AppConfig) -> str:
    rules: list[str] = []
    for name, bucket_cfg in (
        ("work", cfg.importance.work),
        ("personal", cfg.importance.personal),
    ):
        if bucket_cfg.always_flag:
            rules.append(f"In {name}, also treat as urgent: " + ", ".join(bucket_cfg.always_flag) + ".")
        if bucket_cfg.never_flag:
            rules.append(f"In {name}, never urgent: " + ", ".join(bucket_cfg.never_flag) + ".")
    hints_block = (
        "## Per-bucket rules from user config\n" + "\n".join(rules) if rules else ""
    )
    return _SYSTEM_PROMPT.replace(_HINTS_SLOT, hints_block)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_briefing(
    emails: dict[str, list[dict[str, Any]]],
    gcal_events: dict[str, list[dict[str, Any]]],
    icloud_events: dict[str, list[dict[str, Any]]],
    cfg: AppConfig,
) -> str:
    """Generate the final Telegram-HTML briefing in one LLM call.

    Raises RuntimeError on any LLM failure — the caller decides whether to
    send an error message to Telegram.
    """
    total = (
        sum(len(v) for v in emails.values())
        + sum(len(v) for v in gcal_events.values())
        + sum(len(v) for v in icloud_events.values())
    )
    if total == 0:
        return _empty_briefing()

    if shutil.which("claude") is None:
        raise RuntimeError("`claude` CLI not on PATH — cannot generate briefing")

    user_message = _format_input(emails, gcal_events, icloud_events, cfg)
    system_prompt = _build_system_prompt(cfg)
    return _call_claude(system_prompt, user_message, cfg)


def split_for_telegram(briefing: str, max_chars: int = _TELEGRAM_MAX_CHARS) -> list[str]:
    """Split a long briefing into Telegram-sized chunks.

    Prefers splitting at "\\n---\\n" separator lines. Telegram's hard limit
    is 4096 chars per message; we leave headroom.
    """
    if len(briefing) <= max_chars:
        return [briefing]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    sep = "\n---\n"
    for block in briefing.split(sep):
        block_len = len(block) + len(sep)
        if current and current_len + block_len > max_chars:
            chunks.append(sep.join(current))
            current = [block]
            current_len = block_len
        else:
            current.append(block)
            current_len += block_len
    if current:
        chunks.append(sep.join(current))
    return chunks


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_claude(system_prompt: str, user_message: str, cfg: AppConfig) -> str:
    cmd = [
        "claude",
        "-p",
        "--model",
        cfg.llm.model,
        "--system-prompt",
        system_prompt,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--max-budget-usd",
        "1",
        user_message,
    ]
    env = os.environ.copy()
    env["DISABLE_OMC"] = "1"

    log.debug(
        "Invoking claude CLI for briefing (system=%d ch, user=%d ch)",
        len(system_prompt),
        len(user_message),
    )

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SEC,
        env=env,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:300]}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p stdout is not JSON: {exc}") from exc

    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p reported error: {envelope.get('result', '<no msg>')}")

    text = (envelope.get("result") or "").strip()
    if not text:
        raise RuntimeError("claude -p returned empty result")

    return _strip_code_fence(text)


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _empty_briefing() -> str:
    date_str = datetime.now(_KST).strftime("%A, %B %-d, %Y")
    return f"<b>☀️ MORNING BRIEF — {date_str}</b>\n\nNo new mail or events today."


def _format_input(
    emails: dict[str, list[dict[str, Any]]],
    gcal_events: dict[str, list[dict[str, Any]]],
    icloud_events: dict[str, list[dict[str, Any]]],
    cfg: AppConfig,
) -> str:
    date_str = datetime.now(_KST).strftime("%A, %B %-d, %Y")
    sections: list[str] = [f"DATE: {date_str}"]

    accounts_by_id = {a.id: a for a in cfg.accounts}
    work_gmail_ids = [a.id for a in cfg.accounts if a.type == "gmail" and a.bucket == "work"]
    personal_gmail_ids = [a.id for a in cfg.accounts if a.type == "gmail" and a.bucket == "personal"]
    work_gcal_ids = [c.id for c in cfg.calendars if c.type == "gcal" and c.bucket == "work"]
    personal_gcal_ids = [c.id for c in cfg.calendars if c.type == "gcal" and c.bucket == "personal"]

    sections.append(_format_email_section("WORK GMAIL", work_gmail_ids, emails, accounts_by_id))
    sections.append(_format_event_section("WORK GOOGLE CALENDAR (today)", work_gcal_ids, gcal_events))
    sections.append(_format_email_section("PERSONAL GMAIL", personal_gmail_ids, emails, accounts_by_id))
    sections.append(
        _format_event_section("PERSONAL GOOGLE CALENDAR (today)", personal_gcal_ids, gcal_events)
    )
    sections.append(_format_icloud_section(icloud_events))

    return "\n\n".join(sections)


def _format_email_section(
    label: str,
    account_ids: list[str],
    emails: dict[str, list[dict[str, Any]]],
    accounts_by_id: dict[str, Any],
) -> str:
    lines = [f"=== {label} ==="]
    if not account_ids:
        lines.append("(no accounts configured for this bucket)")
        return "\n".join(lines)

    for aid in account_ids:
        items = emails.get(aid) or []
        acc = accounts_by_id.get(aid)
        email_label = acc.email if acc else aid
        lines.append(f"-- {email_label} ({len(items)} unread) --")
        if not items:
            lines.append("(none)")
            continue
        for i, item in enumerate(items, 1):
            sender = item.get("sender", "")
            subject = item.get("subject", "(no subject)")
            snippet = (item.get("snippet") or "").strip()[:300]
            parts = [f"{i}. From: {sender} | Subject: {subject}"]
            if snippet:
                parts.append(f"Snippet: {snippet}")
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _format_event_section(
    label: str,
    calendar_ids: list[str],
    events: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [f"=== {label} ==="]
    total = 0
    for cid in calendar_ids:
        items = events.get(cid) or []
        total += len(items)
        for evt in items:
            lines.append(_format_event_line(evt))
    if total == 0:
        lines.append("(none)")
    return "\n".join(lines)


def _format_icloud_section(icloud_events: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["=== PERSONAL ICLOUD CALENDAR (today) ==="]
    total = 0
    for items in icloud_events.values():
        total += len(items)
        for evt in items:
            lines.append(_format_event_line(evt))
    if total == 0:
        lines.append("(none)")
    return "\n".join(lines)


def _format_event_line(evt: dict[str, Any]) -> str:
    title = evt.get("summary") or "(no title)"
    dtstart = evt.get("dtstart") or ""
    location = (evt.get("location") or "").strip()
    parts = [f"- {dtstart} {title}".strip()]
    if location:
        parts.append(f"@ {location}")
    return " ".join(parts)
