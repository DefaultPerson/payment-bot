from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.config import Settings, load_settings


def test_default_settings_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.bot.fallback_language == "ru"
    assert settings.bot.supported_languages == ["ru", "ua", "en"]
    assert settings.bot.username == "HydraPaymentBot"
    assert settings.http.host == "127.0.0.1"
    assert settings.http.port == 8080
    assert settings.admin_ids == []


def test_phase2_cryptobot_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.cryptobot.enabled is True
    assert settings.cryptobot.token is None
    assert settings.cryptobot.api_url == "https://pay.crypt.bot"
    assert settings.cryptobot.accepted_assets == ["USDT", "TON", "BTC"]
    assert settings.cryptobot.fee_percent == Decimal("3")
    assert settings.cryptobot.invoice_expires_seconds == 3600
    assert settings.cryptobot.poll_interval_seconds == 30
    assert settings.cryptobot.poll_batch_size == 50


def test_phase2_default_tariffs_are_available() -> None:
    settings = Settings()

    assert [tariff.code for tariff in settings.tariffs.items] == [
        "1m",
        "3m",
        "6m",
        "12m",
    ]
    assert [tariff.duration_months for tariff in settings.tariffs.items] == [1, 3, 6, 12]
    assert [tariff.duration_discount_percent for tariff in settings.tariffs.items] == [
        Decimal("0"),
        Decimal("10"),
        Decimal("15"),
        Decimal("25"),
    ]
    assert all(tariff.base_price_usd > 0 for tariff in settings.tariffs.items)
    assert settings.loyalty.bronze_discount_percent == Decimal("10")
    assert settings.loyalty.silver_active_days == 90
    assert settings.loyalty.silver_monthly_price_usd == Decimal("65")
    assert settings.loyalty.gold_active_days == 180
    assert settings.loyalty.gold_monthly_price_usd == Decimal("50")


def test_phase3_promo_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.promos.max_discount_percent == Decimal("50")


def test_phase4_stars_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.stars.enabled is True
    assert settings.stars.app_store_fee_percent == Decimal("30")
    assert settings.stars.star_usd_rate == Decimal("0.013")
    assert settings.stars.invoice_expires_seconds == 3600


def test_phase5_referral_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.referral.percent_default == Decimal("10")
    assert settings.referral.min_withdraw_usd == Decimal("10")
    assert settings.referral.allow_referrer_change is False


def test_phase6_notifications_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.notifications.loop_interval_seconds == 3600
    assert settings.notifications.batch_size == 100
    assert settings.notifications.reminder_days_before == 3
    assert settings.notifications.reminder_window_seconds == 3600
    assert settings.notifications.post_expired_delay_hours == 24
    assert settings.access.revoke_ban_seconds == 60


def test_phase6_notifications_config_rejects_invalid_periods() -> None:
    with pytest.raises(ValidationError):
        Settings(notifications={"loop_interval_seconds": 0})
    with pytest.raises(ValidationError):
        Settings(notifications={"batch_size": 0})
    with pytest.raises(ValidationError):
        Settings(notifications={"reminder_days_before": 0})
    with pytest.raises(ValidationError):
        Settings(notifications={"post_expired_delay_hours": 0})
    with pytest.raises(ValidationError):
        Settings(access={"revoke_ban_seconds": 0})


def test_phase5_referral_config_rejects_invalid_percent() -> None:
    with pytest.raises(ValidationError):
        Settings(referral={"percent_default": Decimal("100")})


def test_phase5_referral_config_rejects_invalid_min_withdrawal() -> None:
    with pytest.raises(ValidationError):
        Settings(referral={"min_withdraw_usd": Decimal("0")})


def test_phase4_stars_config_rejects_invalid_rate() -> None:
    with pytest.raises(ValidationError):
        Settings(stars={"star_usd_rate": Decimal("0")})


def test_phase3_loyalty_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="gold_active_days must be greater"):
        Settings(loyalty={"silver_active_days": 180, "gold_active_days": 90})


def test_phase3_promo_discount_cap_rejects_invalid_percent() -> None:
    with pytest.raises(ValidationError):
        Settings(promos={"max_discount_percent": Decimal("101")})


def test_phase2_tariff_codes_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="tariff codes must be unique"):
        Settings(
            tariffs={
                "items": [
                    {"code": "1m", "duration_months": 1, "base_price_usd": Decimal("79")},
                    {"code": "1m", "duration_months": 3, "base_price_usd": Decimal("237")},
                ]
            }
        )


def test_phase2_access_defaults_are_empty_and_safe() -> None:
    settings = Settings()

    assert settings.access.protected_chats == []
    assert settings.access.invite_link_ttl_seconds == 24 * 60 * 60
    assert settings.access.revoke_ban_seconds == 60


def test_example_env_can_load_with_blank_cryptobot_token() -> None:
    settings = load_settings(config_path="config.example.toml", env_file=".env.example")

    assert settings.cryptobot.token is None


def test_cryptobot_token_env_alias_is_supported(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CRYPTOBOT_TOKEN=crypto-secret\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.cryptobot.token is not None
    assert settings.cryptobot.token.get_secret_value() == "crypto-secret"


def test_cryptobot_token_env_alias_normalizes_empty_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CRYPTOBOT_TOKEN=\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.cryptobot.token is None


def test_cryptobot_token_env_alias_allows_empty_value_when_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [cryptobot]
        enabled = false
        """,
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("CRYPTOBOT_TOKEN=\n", encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_file)

    assert settings.cryptobot.enabled is False
    assert settings.cryptobot.token is None


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
