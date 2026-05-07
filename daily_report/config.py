"""Runtime configuration — loads config.yaml + .env.

Field names mirror config.example.yaml exactly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Sub-models (matching config.example.yaml structure)
# ---------------------------------------------------------------------------


class ScheduleConfig(BaseModel):
    hour: int = 8
    minute: int = 0
    tz: str = "Asia/Seoul"


class OAuthScopesConfig(BaseModel):
    gmail: list[str] = Field(default=["https://www.googleapis.com/auth/gmail.readonly"])
    gcal: list[str] = Field(default=["https://www.googleapis.com/auth/calendar.readonly"])


class OAuthConfig(BaseModel):
    scopes: OAuthScopesConfig = Field(default_factory=OAuthScopesConfig)


class AccountConfig(BaseModel):
    id: str
    type: Literal["gmail"]
    bucket: Literal["personal", "work"]
    email: str
    primary_tab_only: bool = True


class CalendarConfig(BaseModel):
    id: str
    type: Literal["gcal", "icloud"]
    bucket: Literal["personal", "work"]
    # gcal-specific
    account_ref: str | None = None
    # icloud-specific
    calendar_names: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    bot_token_keyring: str = "daily-report/telegram-bot"


class LLMConfig(BaseModel):
    model: str = "claude-haiku-4-5-20251001"
    # No api_key field: classifier shells out to the local `claude -p` CLI,
    # which uses the user's Claude Code session (Team seat). See classifier.py.


class BucketImportanceConfig(BaseModel):
    confidence_threshold: float = 0.5
    always_flag: list[str] = Field(default_factory=list)
    never_flag: list[str] = Field(default_factory=list)


class ImportanceConfig(BaseModel):
    personal: BucketImportanceConfig = Field(default_factory=BucketImportanceConfig)
    work: BucketImportanceConfig = Field(default_factory=BucketImportanceConfig)


class HealthcheckConfig(BaseModel):
    stale_threshold_minutes: int = 30


class AppConfig(BaseModel):
    """Top-level config matching config.example.yaml."""

    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    accounts: list[AccountConfig] = Field(default_factory=list)
    calendars: list[CalendarConfig] = Field(default_factory=list)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    importance: ImportanceConfig = Field(default_factory=ImportanceConfig)
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)


# ---------------------------------------------------------------------------
# .env loader (non-secret routing vars)
# ---------------------------------------------------------------------------


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_chat_id: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_CONFIG_SEARCH: list[Path] = [
    Path("config.yaml"),
    Path.home() / ".config" / "daily-report" / "config.yaml",
]


def load_config(path: Path | None = None) -> AppConfig:
    """Load AppConfig from a YAML file.

    Search order:
      1. Explicit `path` argument (if provided).
      2. ``./config.yaml`` (project root).
      3. ``~/.config/daily-report/config.yaml``.

    Raises FileNotFoundError with a helpful message if none found.
    """
    candidates = [path] if path is not None else _CONFIG_SEARCH

    for candidate in candidates:
        if candidate.exists():
            raw = yaml.safe_load(candidate.read_text())
            return AppConfig.model_validate(raw or {})

    searched = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"No config.yaml found. Searched: {searched}\n"
        "Copy config.example.yaml → config.yaml and fill in your details."
    )


def get_telegram_chat_id(cfg: AppConfig) -> str:
    """Read the Telegram chat ID from the env var named in config."""
    env_key = cfg.telegram.chat_id_env
    value = os.environ.get(env_key, "")
    if not value:
        raise RuntimeError(
            f"Telegram chat ID not set. Add {env_key}=<your-chat-id> to your .env file."
        )
    return value
