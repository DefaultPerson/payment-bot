from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from bot.config import Settings
from bot.core.logger import (
    NOISY_LOGGERS,
    RedactingJsonFormatter,
    collect_secret_values,
    setup_json_logging,
)


def _format(formatter: RedactingJsonFormatter, message: str, **extra: object) -> dict:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(formatter.format(record))


def test_redacts_keys_matching_secret_denylist() -> None:
    formatter = RedactingJsonFormatter("%(message)s")

    out = _format(formatter, "hi", api_token="abc123", note="ok")

    assert out["api_token"] == "[REDACTED]"
    assert out["note"] == "ok"


def test_scrubs_known_secret_value_inside_message_string() -> None:
    formatter = RedactingJsonFormatter("%(message)s", secrets=("supersecrettoken12345",))

    out = _format(formatter, "boom: https://api.telegram.org/botsupersecrettoken12345/getMe")

    assert "supersecrettoken12345" not in out["message"]
    assert "[REDACTED]" in out["message"]


def test_masks_dsn_password_in_message_string() -> None:
    formatter = RedactingJsonFormatter("%(message)s")

    out = _format(formatter, "connect failed: postgresql+asyncpg://user:hunter2@host:5432/db")

    assert "hunter2" not in out["message"]
    assert "user:[REDACTED]@host" in out["message"]


def test_scrubs_secret_inside_nested_dict_value() -> None:
    formatter = RedactingJsonFormatter("%(message)s", secrets=("longsecretvalue123",))

    out = _format(formatter, "x", context={"detail": "key longsecretvalue123 leaked"})

    assert "longsecretvalue123" not in json.dumps(out["context"])


def test_collect_secret_values_keeps_long_tokens_and_skips_short() -> None:
    settings = Settings(BOT_TOKEN="123456:averylongbottoken", CRYPTOBOT_TOKEN="short")

    values = collect_secret_values(settings)

    assert "123456:averylongbottoken" in values
    assert "short" not in values  # below the min scrubbable length


@pytest.fixture
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_noisy = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        for name, level in saved_noisy.items():
            logging.getLogger(name).setLevel(level)


def test_noisy_loggers_muted_to_warning_in_production(tmp_path, _restore_logging) -> None:
    settings = Settings(LOG_LEVEL="INFO", logging={"file_path": str(tmp_path / "app.log")})

    setup_json_logging(settings)

    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_noisy_loggers_open_to_info_in_debug(tmp_path, _restore_logging) -> None:
    settings = Settings(LOG_LEVEL="DEBUG", logging={"file_path": str(tmp_path / "app.log")})

    setup_json_logging(settings)

    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO
