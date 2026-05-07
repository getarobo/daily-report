"""State persistence — ~/Library/Application Support/daily-report/state.json."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_STATE_DIR = Path.home() / "Library" / "Application Support" / "daily-report"
_STATE_FILE = _STATE_DIR / "state.json"


def _state_path() -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_FILE


def load_state() -> dict:
    """Return the current state dict, or {} on first run / missing file."""
    p = _state_path()
    if not p.exists():
        log.debug("State file not found; starting fresh at %s", p)
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read state file (%s); starting fresh", exc)
        return {}


def save_state(state: dict) -> None:
    """Persist *state* dict to disk atomically (write-then-rename)."""
    p = _state_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(p)
    log.debug("State saved to %s", p)


def get_history_id(state: dict, account_id: str) -> str | None:
    """Return the Gmail historyId watermark for *account_id*, or None."""
    return state.get("accounts", {}).get(account_id, {}).get("history_id")


def set_history_id(state: dict, account_id: str, history_id: str) -> None:
    """Update the Gmail historyId watermark in-place."""
    state.setdefault("accounts", {}).setdefault(account_id, {})["history_id"] = history_id


def get_last_run_ts(state: dict) -> datetime | None:
    """Return last_run_ts as UTC datetime, or None."""
    raw = state.get("last_run_ts")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


def set_last_run_ts(state: dict, ts: datetime | None = None) -> None:
    """Set last_run_ts to *ts* (defaults to now UTC)."""
    if ts is None:
        ts = datetime.now(UTC)
    state["last_run_ts"] = ts.isoformat()
