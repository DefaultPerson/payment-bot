from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.config import Settings, load_settings


def test_default_settings_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.bot.fallback_language == "ru"
    assert settings.bot.supported_languages == ["ru", "ua", "en"]
    assert settings.http.host == "127.0.0.1"
    assert settings.http.port == 8080
    assert settings.admin_ids == []


def test_admin_ids_parse_from_csv() -> None:
    settings = Settings(ADMIN_IDS="10, 20,30")

    assert settings.admin_ids == [10, 20, 30]


def test_admin_ids_parse_from_env_file_csv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_IDS=7,8\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.admin_ids == [7, 8]


def test_load_settings_reads_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [bot]
        fallback_language = "en"
        supported_languages = ["en", "ru"]
        support_username = "HMonitorsBot"

        [service_channel]
        chat_id = -100123
        thread_id = 7

        [http]
        host = "127.0.0.1"
        port = 9090
        """,
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, env_file=None)

    assert settings.bot.fallback_language == "en"
    assert settings.service_channel.chat_id == -100123
    assert settings.service_channel.thread_id == 7
    assert settings.http.port == 9090


def test_env_file_overrides_toml_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [http]
        port = 9090
        """,
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("HTTP__PORT=8081\n", encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_file)

    assert settings.http.port == 8081


def test_runtime_validation_rejects_missing_service_channel() -> None:
    settings = Settings(BOT_TOKEN="123456:abcdef", DATABASE_URL="postgresql+asyncpg://u:p@h/db")

    with pytest.raises(ValueError, match="SERVICE_CHANNEL_ID"):
        settings.validate_runtime()


def test_runtime_validation_rejects_test_bot_token() -> None:
    settings = Settings(service_channel={"chat_id": -100123})

    with pytest.raises(ValueError, match="BOT_TOKEN"):
        settings.validate_runtime()


def test_runtime_validation_rejects_placeholder_bot_token() -> None:
    settings = Settings(BOT_TOKEN="123456:replace-me", service_channel={"chat_id": -100123})

    with pytest.raises(ValueError, match="BOT_TOKEN"):
        settings.validate_runtime()


def test_flat_service_channel_env_aliases_are_supported(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=123456:abcdef",
                "DATABASE_URL=postgresql+asyncpg://u:p@h/db",
                "SERVICE_CHANNEL_ID=-100123",
                "SERVICE_THREAD_ID=7",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.service_channel.chat_id == -100123
    assert settings.service_channel.thread_id == 7
    settings.validate_runtime()


def test_invalid_language_fallback_fails() -> None:
    with pytest.raises(ValidationError):
        Settings(bot={"supported_languages": ["ru"], "fallback_language": "en"})
