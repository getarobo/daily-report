"""Healthcheck — Phase 4 stub.

Reads state.last_run_ts; if stale > threshold, pings Telegram.
Currently a stub that exits 0 with a notice message.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run_healthcheck() -> None:
    """Phase 4 stub — prints notice and exits 0."""
    print("(stub) healthcheck: not yet implemented (Phase 4).")
