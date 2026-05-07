# daily-report — developer recipes.
# Run `just` to list. Assumes Python 3.11+.

set shell := ["bash", "-cu"]

# Install backend (.venv) deps. Picks the newest Python >= 3.11 available.
install:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d .venv ]; then
      PY=""
      for v in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$v" >/dev/null 2>&1; then
          if "$v" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PY="$v"; break
          fi
        fi
      done
      if [ -z "$PY" ]; then
        echo "ERROR: need Python >= 3.11 (try: brew install python@3.13)"; exit 1
      fi
      echo "Creating .venv with $PY"
      "$PY" -m venv .venv
    fi
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e '.[dev]'

# Print the rendered HTML digest to stdout (no Telegram send). Use this for
# day-to-day checks of classifier/digest output without spamming the bot.
dry-run:
    .venv/bin/python -m daily_report run --dry-run

# Same as dry-run but ALSO sends to Telegram (for end-to-end smoke).
dry-run-notify:
    .venv/bin/python -m daily_report run --dry-run --notify

# Real run (what launchd invokes).
run:
    .venv/bin/python -m daily_report run

# Healthcheck: pings Telegram if state.last_run_ts is stale.
healthcheck:
    .venv/bin/python -m daily_report healthcheck

# Interactive OAuth for one Google account. Args: e.g. `just auth-google gene@gmail.com`.
auth-google email:
    .venv/bin/python -m daily_report auth-google {{email}}

# Lint + format check.
lint:
    .venv/bin/ruff check .
    .venv/bin/ruff format --check .

# Format in place.
fmt:
    .venv/bin/ruff format .

# Run the test suite.
test:
    .venv/bin/pytest -q
