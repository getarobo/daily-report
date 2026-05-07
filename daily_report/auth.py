"""Google OAuth helpers.

Credentials are stored in macOS Keychain via `keyring` under:
  service  = "daily-report"
  username = "google:<email>"

Client secrets are read from:
  ~/.config/daily-report/google-client.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import keyring
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "daily-report"
_CLIENT_JSON = Path.home() / ".config" / "daily-report" / "google-client.json"


def _keyring_key(email: str) -> str:
    return f"google:{email}"


def run_oauth_flow(email: str, scopes: list[str]) -> None:
    """Run the InstalledAppFlow for *email* and store credentials in Keychain.

    Reads the client secret from ``~/.config/daily-report/google-client.json``.
    """
    if not _CLIENT_JSON.exists():
        raise FileNotFoundError(
            f"Google client JSON not found at {_CLIENT_JSON}.\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials\n"
            "and save it to that path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_JSON), scopes=scopes)
    creds = flow.run_local_server(port=0)

    creds_dict = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or scopes),
    }
    keyring.set_password(_KEYRING_SERVICE, _keyring_key(email), json.dumps(creds_dict))
    log.info("Credentials for %s stored in Keychain.", email)
    print(f"OAuth complete — credentials for {email} saved to Keychain.")


def load_credentials(email: str) -> Credentials:
    """Load and return Google credentials from Keychain for *email*.

    Raises RuntimeError with a clear "no creds" message if not found.
    """
    raw = keyring.get_password(_KEYRING_SERVICE, _keyring_key(email))
    if not raw:
        raise RuntimeError(
            f"No credentials found for {email}.\nRun: python -m daily_report auth-google {email}"
        )
    data = json.loads(raw)
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )
