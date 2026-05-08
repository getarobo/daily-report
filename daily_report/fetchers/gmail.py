"""Gmail fetcher — per-account, with historyId watermark + date fallback."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from daily_report.auth import load_credentials
from daily_report.config import AccountConfig
from daily_report.state import get_history_id, set_history_id

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_gmail(
    accounts: list[AccountConfig],
    state: dict,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch new emails for each account in *accounts*.

    Returns a mapping of ``account_id -> list of message dicts``.
    Each message dict has keys: ``id, subject, sender, snippet, date, bucket``.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        try:
            msgs = _fetch_account(account, state)
            results[account.id] = msgs
        except RuntimeError as exc:
            # Re-raise auth errors loudly (plan principle #5: Fail loud)
            raise RuntimeError(f"Gmail fetch failed for {account.email}: {exc}") from exc
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_account(account: AccountConfig, state: dict) -> list[dict[str, Any]]:
    """Fetch messages for a single account, updating state in-place."""
    creds = load_credentials(account.email)

    # Refresh token if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        log.info("Refreshed credentials for %s", account.email)

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    history_id = get_history_id(state, account.id)

    if history_id:
        messages, new_history_id = _fetch_by_history(service, account, history_id)
    else:
        messages, new_history_id = _fetch_by_date(service, account)

    if new_history_id:
        set_history_id(state, account.id, new_history_id)

    log.info("Fetched %d messages for %s", len(messages), account.email)
    return messages


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_by_history(
    service: Any,
    account: AccountConfig,
    history_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch messages since *history_id*.  Returns (messages, new_history_id)."""
    try:
        response = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
            )
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            log.warning(
                "historyId %s expired for %s, falling back to date query",
                history_id,
                account.email,
            )
            return _fetch_by_date(service, account)
        raise

    messages_raw: list[dict] = []
    for record in response.get("history", []):
        for m in record.get("messagesAdded", []):
            msg = m.get("message", {})
            label_ids = msg.get("labelIds", [])
            if "UNREAD" not in label_ids:
                continue
            if account.primary_tab_only and "CATEGORY_PERSONAL" not in label_ids:
                continue
            messages_raw.append(msg)

    new_history_id: str | None = response.get("historyId")
    messages = _fetch_message_details(service, account, messages_raw)
    return messages, new_history_id


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_by_date(
    service: Any,
    account: AccountConfig,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fall-back fetch: query primary unread from last 24h."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y/%m/%d")
    query_parts = [f"after:{yesterday}", "is:unread"]
    if account.primary_tab_only:
        query_parts.append("category:primary")

    query = " ".join(query_parts)
    log.info("Fetching Gmail for %s with query: %s", account.email, query)

    response = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages_raw = response.get("messages", [])

    # Grab current historyId from profile
    profile = service.users().getProfile(userId="me").execute()
    new_history_id: str | None = profile.get("historyId")

    messages = _fetch_message_details(service, account, messages_raw)
    return messages, new_history_id


def _fetch_message_details(
    service: Any,
    account: AccountConfig,
    messages_raw: list[dict],
) -> list[dict[str, Any]]:
    """Fetch full details for each message stub and return enriched dicts."""
    results = []
    for stub in messages_raw:
        msg_id = stub.get("id")
        if not msg_id:
            continue
        try:
            detail = _get_message(service, msg_id)
        except HttpError as exc:
            log.warning("Could not fetch message %s: %s", msg_id, exc)
            continue

        headers = {
            h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])
        }
        results.append(
            {
                "id": msg_id,
                "subject": headers.get("subject", "(no subject)"),
                "sender": headers.get("from", ""),
                "snippet": detail.get("snippet", ""),
                "date": headers.get("date", ""),
                "bucket": account.bucket,
                "account_id": account.id,
            }
        )
    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get_message(service: Any, msg_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["Subject", "From", "Date"])
        .execute()
    )
