from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from pythonjsonlogger.json import JsonFormatter

from bot.config import Settings

SECRET_KEY_PARTS = ("token", "secret", "password", "authorization", "cookie")

# Third-party loggers that are chatty at INFO (SQLAlchemy logs every statement +
# bound params, aiogram/aiohttp log startup and per-request lines). Keep them at
# WARNING in production; only open them up when the app itself runs at DEBUG.
NOISY_LOGGERS = ("sqlalchemy", "aiogram", "aiohttp", "apscheduler")

# Mask the password in any DSN-like URL that lands in a free-text log field
# (e.g. asyncpg/SQLAlchemy connection errors echo the DATABASE_URL).
_DSN_PASSWORD = re.compile(r"(://[^\s:/@]+:)([^\s/@]+)(@)")

# Exact secret values shorter than this are not scrubbed by value — too likely to
# collide with ordinary words (DSN passwords are handled by the regex above instead).
_MIN_SCRUBBABLE_SECRET_LEN = 8


class RedactingJsonFormatter(JsonFormatter):
    def __init__(self, *args: Any, secrets: Iterable[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._secrets = tuple(dict.fromkeys(s for s in secrets if s))

    def process_log_record(self, log_record: dict[str, Any]) -> dict[str, Any]:
        for key, value in list(log_record.items()):
            if any(part in key.lower() for part in SECRET_KEY_PARTS):
                log_record[key] = "[REDACTED]"
            elif isinstance(value, dict):
                log_record[key] = self._redact_dict(value)
            elif isinstance(value, str):
                log_record[key] = self._scrub_value(value)
        return super().process_log_record(log_record)

    def _redact_dict(self, values: dict[Any, Any]) -> dict[Any, Any]:
        redacted: dict[Any, Any] = {}
        for key, value in values.items():
            if any(part in str(key).lower() for part in SECRET_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                redacted[key] = self._redact_dict(value)
            elif isinstance(value, str):
                redacted[key] = self._scrub_value(value)
            else:
                redacted[key] = value
        return redacted

    def _scrub_value(self, text: str) -> str:
        """Scrub known secret values and DSN passwords from a free-text field."""
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED]")
        return _DSN_PASSWORD.sub(r"\1[REDACTED]\3", text)


def collect_secret_values(settings: Settings) -> tuple[str, ...]:
    """Long-enough secret values to scrub verbatim from any string log field."""
    candidates: list[str] = [settings.bot_token.get_secret_value()]
    for token in (
        settings.cryptobot.token,
        settings.xrocket.token,
        settings.cryptobot_testnet_token,
        settings.xrocket_testnet_token,
    ):
        if token is not None:
            candidates.append(token.get_secret_value())
    return tuple(
        dict.fromkeys(c for c in candidates if c and len(c) >= _MIN_SCRUBBABLE_SECRET_LEN)
    )


def setup_json_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level)
    formatter = RedactingJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        secrets=collect_secret_values(settings),
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    log_path = Path(settings.logging.file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.logging.max_bytes,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(level)
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(file_handler)

    # WARNING in production, INFO in dev (when LOG_LEVEL=DEBUG) — see task.md §11.
    noisy_level = logging.INFO if level <= logging.DEBUG else logging.WARNING
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy_level)


def _compact_html(text: str) -> str:
    # Strip each line but keep intentional single blank lines as section separators
    # (collapse runs of blanks to one; drop leading/trailing blanks).
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


class ServiceChannel:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        *,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = settings.service_channel.chat_id
        self.thread_id = settings.service_channel.thread_id
        self.logger = logger_ or logging.getLogger(__name__)

    async def send_html(self, text: str) -> bool:
        if self.chat_id is None:
            self.logger.warning("service channel is not configured")
            return False

        compact_text = _compact_html(text)
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                message_thread_id=self.thread_id,
                text=compact_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            self.logger.exception(
                "failed to send service channel message",
                extra={"chat_id": self.chat_id, "thread_id": self.thread_id},
            )
            return False
        return True
