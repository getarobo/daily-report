"""CLI entry point: python -m daily_report <subcommand>

Subcommands:
  run            Fetch, classify, render and optionally send the digest.
  healthcheck    Check that the last run was recent (Phase 4 stub).
  auth-google    Run OAuth flow for a Google account.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — sets unset env vars from key=value lines."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence chatty third-party loggers. httpx in particular logs the full
    # Telegram URL — which embeds the bot token — at INFO level.
    for noisy in ("httpx", "google_auth_httplib2", "googleapiclient.discovery"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _cmd_run(args: argparse.Namespace) -> int:
    """Fetch → classify → render → (optionally) send."""
    # Load .env for TELEGRAM_CHAT_ID etc. (simple manual parse; avoids python-dotenv dep)
    _load_dotenv(Path(".env"))

    from daily_report.config import load_config
    from daily_report.state import load_state, save_state, set_last_run_ts

    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    state = load_state()

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    from daily_report.fetchers.gcal import fetch_gcal
    from daily_report.fetchers.gmail import fetch_gmail
    from daily_report.fetchers.icloud import fetch_icloud

    # P3: use all accounts (shape ready for N; currently typically 1-2)
    gmail_accounts = [a for a in cfg.accounts if a.type == "gmail"]

    try:
        emails = fetch_gmail(gmail_accounts, state)
    except RuntimeError as exc:
        msg = str(exc)
        logging.getLogger(__name__).error("Gmail fetch error: %s", msg)
        if not args.dry_run:
            from daily_report.notify.telegram import send_error

            send_error(msg, cfg)
        print(f"ERROR (Gmail): {msg}", file=sys.stderr)
        return 1

    try:
        gcal_events = fetch_gcal(cfg.calendars, cfg.accounts)
    except RuntimeError as exc:
        msg = str(exc)
        logging.getLogger(__name__).error("GCal fetch error: %s", msg)
        if not args.dry_run:
            from daily_report.notify.telegram import send_error

            send_error(msg, cfg)
        print(f"ERROR (GCal): {msg}", file=sys.stderr)
        return 1

    # iCloud is best-effort — never aborts the run
    icloud_events = fetch_icloud(cfg.calendars)

    # ------------------------------------------------------------------
    # Classify
    # ------------------------------------------------------------------
    from daily_report.classifier import classify_items

    for account_id, msgs in emails.items():
        # Determine bucket from account config
        account = next((a for a in cfg.accounts if a.id == account_id), None)
        bucket = account.bucket if account else "personal"
        classify_items(msgs, bucket, cfg)

    for cal_id, evts in gcal_events.items():
        cal = next((c for c in cfg.calendars if c.id == cal_id), None)
        bucket = cal.bucket if cal else "personal"
        classify_items(evts, bucket, cfg)

    for _cal_id, evts in icloud_events.items():
        classify_items(evts, "personal", cfg)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    from daily_report.digest import render_digest

    messages = render_digest(emails, gcal_events, icloud_events)

    # ------------------------------------------------------------------
    # Output / send
    # ------------------------------------------------------------------
    dry_run: bool = args.dry_run
    notify: bool = getattr(args, "notify", False)

    if dry_run and not notify:
        # Print HTML to stdout for visual inspection
        print("\n\n--- MESSAGE BREAK ---\n\n".join(messages))
    else:
        from daily_report.notify.telegram import send_messages

        send_messages(messages, cfg)
        logging.getLogger(__name__).info("Digest sent to Telegram (%d message(s)).", len(messages))

    # ------------------------------------------------------------------
    # Update state
    # ------------------------------------------------------------------
    set_last_run_ts(state)
    save_state(state)

    return 0


def _cmd_healthcheck(_args: argparse.Namespace) -> int:
    from daily_report.healthcheck import run_healthcheck

    run_healthcheck()
    return 0


def _cmd_auth_google(args: argparse.Namespace) -> int:
    _load_dotenv(Path(".env"))

    from daily_report.auth import run_oauth_flow
    from daily_report.config import load_config

    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    email: str = args.email
    scopes = cfg.oauth.scopes.gmail + cfg.oauth.scopes.gcal

    try:
        run_oauth_flow(email, scopes)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during OAuth: {exc}", file=sys.stderr)
        return 1

    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="daily_report",
        description="Personal daily briefing bot — Gmail + GCal + iCloud → Telegram.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser("run", help="Fetch, classify, render, and (optionally) send digest.")
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print HTML to stdout; do NOT send to Telegram.",
    )
    run_p.add_argument(
        "--notify",
        action="store_true",
        help="When used with --dry-run, also send to Telegram (smoke test).",
    )
    run_p.set_defaults(func=_cmd_run)

    # healthcheck
    hc_p = sub.add_parser("healthcheck", help="Check last-run staleness (Phase 4 stub).")
    hc_p.set_defaults(func=_cmd_healthcheck)

    # auth-google
    auth_p = sub.add_parser("auth-google", help="Run OAuth flow for a Google account.")
    auth_p.add_argument("email", help="Google account email to authorise.")
    auth_p.set_defaults(func=_cmd_auth_google)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
