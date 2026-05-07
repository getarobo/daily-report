"""Importance classifier — calls Claude Haiku 4.5 via the Claude Code CLI.

Why subprocess to ``claude -p`` instead of the Anthropic SDK:
the user is on a Claude Team subscription and does not have a separate
Anthropic API key. ``claude -p`` uses the locally-logged-in Claude Code
session, billing the call against the user's Team seat.

Returns per-item: {flag: bool, summary: str, confidence: float}.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from daily_report.config import AppConfig, BucketImportanceConfig

log = logging.getLogger(__name__)

_CLI_TIMEOUT_SEC = 120


# ---------------------------------------------------------------------------
# System prompt (passed via ``--system-prompt``; Claude Code caches it server-side)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a personal email and calendar triage assistant. Your job is to decide
whether each item (email or calendar event) warrants the user's *immediate*
attention today, and to write a concise one-line summary.

## Classification rules

**Always flag (confidence >= 0.9):**
{always_flag_list}

**Never flag (confidence = 0.0):**
{never_flag_list}

**General guidelines:**
- A reply from a known human → flag.
- A calendar invite or event scheduled today → flag.
- A subject mentioning "deadline", "today", "EOD", "urgent", or a specific time today → flag.
- Mass mail, promotional email, newsletters → do NOT flag.
- Receipts, shipping notifications, order confirmations → do NOT flag.
- Automated system alerts (GitHub, Jira, Slack, CI/CD, monitoring) → do NOT flag.
- When uncertain, err on the side of flagging (recall > precision).

**Language:** Write the summary in the SAME language as the source. If the
subject/snippet is Korean, the summary must be Korean. If English, English.
Never translate; preserve the original language so the user reads it natively.

## Output format

Respond with ONLY a JSON array (one object per item, in the same order):
[
  {{"flag": true|false, "summary": "one-line summary <= 80 chars", "confidence": 0.0-1.0}},
  ...
]

Do not include any text outside the JSON array. Do not wrap the JSON in ```code fences.
"""


def _build_system_prompt(bucket_cfg: BucketImportanceConfig) -> str:
    always_list = "\n".join(f"- {x}" for x in bucket_cfg.always_flag)
    never_list = "\n".join(f"- {x}" for x in bucket_cfg.never_flag)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        always_flag_list=always_list or "(none defined)",
        never_flag_list=never_list or "(none defined)",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_items(
    items: list[dict[str, Any]],
    bucket: str,
    cfg: AppConfig,
) -> list[dict[str, Any]]:
    """Classify a list of email/event dicts for the given bucket.

    If the ``claude`` CLI is not installed or the call fails, returns the
    input unchanged with flag=False — the digest still renders, just unranked.
    """
    if not items:
        return items

    bucket_cfg: BucketImportanceConfig | None = getattr(cfg.importance, bucket, None)
    if bucket_cfg is None:
        log.warning("Unknown bucket %r — using defaults", bucket)
        bucket_cfg = BucketImportanceConfig()

    if shutil.which("claude") is None:
        log.warning("`claude` CLI not on PATH — falling back to raw digest")
        return _fallback_classify(items)

    try:
        results = _call_classifier(items, bucket_cfg, cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("Classifier failed (%s) — falling back to raw digest", exc)
        return _fallback_classify(items)

    for item, result in zip(items, results, strict=True):
        threshold = bucket_cfg.confidence_threshold
        item["flag"] = result.get("flag", False) and result.get("confidence", 0.0) >= threshold
        item["confidence"] = result.get("confidence", 0.0)
        llm_summary = result.get("summary", "")
        # Events arrive with a meaningful calendar title in `summary` — don't
        # clobber it. Stash the LLM's one-liner in `note` instead.
        if "dtstart" in item:
            item["note"] = llm_summary
        else:
            item["summary"] = llm_summary or item.get("subject") or ""

    return items


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_classifier(
    items: list[dict[str, Any]],
    bucket_cfg: BucketImportanceConfig,
    cfg: AppConfig,
) -> list[dict[str, Any]]:
    """Invoke ``claude -p`` and parse the JSON envelope it prints."""
    system_prompt = _build_system_prompt(bucket_cfg)
    user_message = _format_items(items)

    cmd = [
        "claude",
        "-p",
        "--model",
        cfg.llm.model,
        "--system-prompt",
        system_prompt,
        "--output-format",
        "json",
        # Don't persist these one-off classification sessions.
        "--no-session-persistence",
        # Hard cap to keep a runaway from chewing the seat.
        "--max-budget-usd",
        "1",
        user_message,
    ]

    # Strip OMC noise so the cron run is clean and reproducible.
    env = os.environ.copy()
    env["DISABLE_OMC"] = "1"

    log.debug("Invoking claude CLI with %d items in bucket %s", len(items), bucket_cfg)
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

    raw_text = (envelope.get("result") or "").strip()
    if not raw_text:
        raise RuntimeError("claude -p returned empty result")

    # Strip a stray ```json fence if Claude added one despite the instruction.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].lstrip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.warning("Classifier returned non-JSON: %r", raw_text[:200])
        raise RuntimeError(f"Non-JSON inner response: {exc}") from exc

    if not isinstance(parsed, list) or len(parsed) != len(items):
        raise RuntimeError(
            f"Classifier returned {len(parsed) if isinstance(parsed, list) else type(parsed)} "
            f"items for {len(items)} inputs"
        )

    return parsed


def _format_items(items: list[dict[str, Any]]) -> str:
    user_lines = []
    for i, item in enumerate(items, 1):
        title = item.get("subject") or item.get("summary", "(no title)")
        sender = item.get("sender", "")
        snippet = item.get("snippet") or item.get("description", "")
        parts = [f"Item {i}: {title}"]
        if sender:
            parts.append(f"From: {sender}")
        if snippet:
            parts.append(f"Snippet: {snippet[:300]}")
        user_lines.append("\n".join(parts))
    return "\n\n---\n\n".join(user_lines)


def _fallback_classify(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-LLM fallback: no flagging, use subject/summary as-is."""
    for item in items:
        item.setdefault("flag", False)
        item.setdefault("summary", item.get("subject") or item.get("summary", ""))
        item.setdefault("confidence", 0.0)
    return items
