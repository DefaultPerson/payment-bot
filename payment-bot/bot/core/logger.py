from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from pythonjsonlogger.json import JsonFormatter

from bot.config import Settings

SECRET_KEY_PARTS = ("token", "secret", "password", "authorization", "cookie")


class RedactingJsonFormatter(JsonFormatter):
    def process_log_record(self, log_record: dict[str, Any]) -> dict[str, Any]:
        for key, value in list(log_record.items()):
            if any(part in key.lower() for part in SECRET_KEY_PARTS):
                log_record[key] = "[REDACTED]"
            elif isinstance(value, dict):
                log_record[key] = self._redact_dict(value)
        return super().process_log_record(log_record)

    def _redact_dict(self, values: dict[Any, Any]) -> dict[Any, Any]:
        redacted: dict[Any, Any] = {}
        for key, value in values.items():
            if any(part in str(key).lower() for part in SECRET_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                redacted[key] = self._redact_dict(value)
            else:
                redacted[key] = value
        return redacted


def setup_json_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level)
    formatter = RedactingJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
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


def _compact_html(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


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
