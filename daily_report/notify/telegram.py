"""Telegram sender — HTML parse mode, 3500-char limit enforced by digest.py."""

from __future__ import annotations

import asyncio
import logging

import keyring
from telegram import Bot
from telegram.constants import ParseMode
from tenacity import retry, stop_after_attempt, wait_exponential

from daily_report.config import AppConfig, get_telegram_chat_id

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "daily-report"


def send_messages(messages: list[str], cfg: AppConfig) -> None:
    """Send each message in *messages* to the configured Telegram chat.

    Reads bot token from Keychain and chat_id from env.
    Raises RuntimeError with a clear message if either is missing.
    """
    bot_token = keyring.get_password(_KEYRING_SERVICE, cfg.telegram.bot_token_keyring)
    if not bot_token:
        raise RuntimeError(
            f"Telegram bot token not found in Keychain at key "
            f"{cfg.telegram.bot_token_keyring!r}.\n"
            "Store it with: keyring set daily-report daily-report/telegram-bot"
        )

    chat_id = get_telegram_chat_id(cfg)
    asyncio.run(_send_all(bot_token, chat_id, messages))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _send_message(bot: Bot, chat_id: str, text: str) -> None:
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    log.info("Sent Telegram message (%d chars) to %s", len(text), chat_id)


async def _send_all(bot_token: str, chat_id: str, messages: list[str]) -> None:
    async with Bot(token=bot_token) as bot:
        for msg in messages:
            await _send_message(bot, chat_id, msg)


def send_error(text: str, cfg: AppConfig) -> None:
    """Best-effort error ping to Telegram. Never raises."""
    try:
        send_messages([f"⚠️ daily-report error:\n{text}"], cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not send Telegram error ping: %s", exc)
