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

    assert [plan.code for plan in settings.tariffs.plans] == ["main"]
    assert settings.tariffs.default_plan_code == "main"
    assert [tariff.code for tariff in settings.tariffs.items] == [
        "1m",
        "3m",
        "6m",
    ]
    assert [tariff.plan_code for tariff in settings.tariffs.items] == ["main", "main", "main"]
    assert [tariff.duration_months for tariff in settings.tariffs.items] == [1, 3, 6]
    assert [tariff.duration_discount_percent for tariff in settings.tariffs.items] == [
        Decimal("0"),
        Decimal("5"),
        Decimal("10"),
    ]
    assert [tariff.base_price_usd for tariff in settings.tariffs.items] == [
        Decimal("59"),
        Decimal("177"),
        Decimal("354"),
    ]
    assert settings.loyalty.bronze_active_months == 2
    assert settings.loyalty.bronze_discount_percent == Decimal("5")
    assert settings.loyalty.silver_active_months == 4
    assert settings.loyalty.silver_discount_percent == Decimal("10")
    assert settings.loyalty.gold_active_months == 6
    assert settings.loyalty.gold_discount_percent == Decimal("15")


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


def test_phase7_xrocket_config_defaults_are_disabled_and_safe() -> None:
    settings = Settings()

    assert settings.xrocket.enabled is False
    assert settings.xrocket.token is None
    assert settings.xrocket.testnet is False
    assert settings.xrocket.base_url is None
    assert settings.xrocket.currency == "USDT"
    assert settings.xrocket.fee_percent == Decimal("0")
    assert settings.xrocket.invoice_expires_seconds == 3600
    assert settings.xrocket.poll_interval_seconds == 30
    assert settings.xrocket.poll_batch_size == 50


def test_phase8_observability_config_defaults_are_safe_for_tests() -> None:
    settings = Settings()

    assert settings.observability.invoice_cleanup_interval_seconds == 300
    assert settings.observability.metrics_snapshot_interval_seconds == 60


def test_phase8_observability_config_rejects_invalid_periods() -> None:
    with pytest.raises(ValidationError):
        Settings(observability={"invoice_cleanup_interval_seconds": 0})
    with pytest.raises(ValidationError):
        Settings(observability={"metrics_snapshot_interval_seconds": 0})


def test_phase7_xrocket_config_rejects_invalid_periods_and_fee() -> None:
    with pytest.raises(ValidationError):
        Settings(xrocket={"fee_percent": Decimal("100")})
    with pytest.raises(ValidationError):
        Settings(xrocket={"invoice_expires_seconds": 0})
    with pytest.raises(ValidationError):
        Settings(xrocket={"invoice_expires_seconds": 86_401})
    with pytest.raises(ValidationError):
        Settings(xrocket={"poll_interval_seconds": 0})
    with pytest.raises(ValidationError):
        Settings(xrocket={"poll_batch_size": 0})


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


def test_phase3_loyalty_months_must_strictly_increase() -> None:
    with pytest.raises(ValidationError, match="active_months must strictly increase"):
        Settings(
            loyalty={
                "bronze_active_months": 2,
                "silver_active_months": 6,
                "gold_active_months": 4,
            }
        )


def test_phase3_loyalty_discount_percents_must_not_decrease() -> None:
    with pytest.raises(ValidationError, match="discount percents must be non-decreasing"):
        Settings(
            loyalty={
                "bronze_discount_percent": Decimal("10"),
                "silver_discount_percent": Decimal("5"),
                "gold_discount_percent": Decimal("15"),
            }
        )


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


def test_phase6_at_least_one_plan_required() -> None:
    with pytest.raises(ValidationError, match="at least one tariff plan"):
        Settings(tariffs={"plans": [], "items": []})


def test_phase6_plan_codes_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="plan codes must be unique"):
        Settings(
            tariffs={
                "plans": [{"code": "main"}, {"code": "main"}],
                "items": [],
            }
        )


def test_phase6_tariff_plan_code_must_reference_configured_plan() -> None:
    with pytest.raises(ValidationError, match="must reference a configured plan: pro"):
        Settings(
            tariffs={
                "plans": [{"code": "main"}],
                "items": [
                    {
                        "code": "1m",
                        "plan_code": "pro",
                        "duration_months": 1,
                        "base_price_usd": Decimal("59"),
                    }
                ],
            }
        )


def test_phase6_plan_chat_ids_and_default_plan_resolution() -> None:
    settings = Settings(
        tariffs={
            "plans": [
                {"code": "lite", "active": False, "chat_ids": [-100]},
                {"code": "pro", "chat_ids": [-200, -300]},
            ],
            "items": [
                {
                    "code": "pro_1m",
                    "plan_code": "pro",
                    "duration_months": 1,
                    "base_price_usd": Decimal("59"),
                }
            ],
        }
    )

    # default_plan_code skips the inactive plan and picks the first active one.
    assert settings.tariffs.default_plan_code == "pro"
    assert settings.tariffs.plans[1].chat_ids == [-200, -300]


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


def test_xrocket_token_env_alias_is_supported(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("XROCKET_TOKEN=xrocket-secret\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.xrocket.token is not None
    assert settings.xrocket.token.get_secret_value() == "xrocket-secret"


def test_xrocket_token_env_alias_normalizes_empty_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("XROCKET_TOKEN=\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.xrocket.token is None


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


def test_payments_test_mode_defaults_off() -> None:
    settings = Settings()

    assert settings.payments.test_mode is False
    assert settings.cryptobot.api_url == "https://pay.crypt.bot"
    assert settings.cryptobot.testnet_api_url == "https://testnet-pay.crypt.bot"
    assert settings.xrocket.testnet is False


def test_payments_test_mode_routes_providers_to_testnet(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [payments]
        test_mode = true

        [cryptobot]
        enabled = true

        [xrocket]
        enabled = true
        """,
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CRYPTOBOT_TOKEN=prod-crypto",
                "CRYPTOBOT_TESTNET_TOKEN=test-crypto",
                "XROCKET_TOKEN=prod-xrocket",
                "XROCKET_TESTNET_TOKEN=test-xrocket",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, env_file=env_file)

    assert settings.payments.test_mode is True
    assert settings.cryptobot.api_url == "https://testnet-pay.crypt.bot"
    assert settings.cryptobot.token is not None
    assert settings.cryptobot.token.get_secret_value() == "test-crypto"
    assert settings.xrocket.testnet is True
    assert settings.xrocket.base_url == "https://pay.testnet.xrocket.exchange"
    assert settings.xrocket.token is not None
    assert settings.xrocket.token.get_secret_value() == "test-xrocket"


def test_payments_prod_mode_keeps_live_endpoints(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CRYPTOBOT_TOKEN=prod-crypto",
                "CRYPTOBOT_TESTNET_TOKEN=test-crypto",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.payments.test_mode is False
    assert settings.cryptobot.api_url == "https://pay.crypt.bot"
    assert settings.cryptobot.token is not None
    assert settings.cryptobot.token.get_secret_value() == "prod-crypto"


def test_runtime_validation_requires_testnet_tokens_in_test_mode() -> None:
    settings = Settings(
        BOT_TOKEN="123456:abcdef",
        DATABASE_URL="postgresql+asyncpg://u:p@h/db",
        service_channel={"chat_id": -100123},
        payments={"test_mode": True},
        cryptobot={"enabled": True},
        xrocket={"enabled": True},
    )

    with pytest.raises(ValueError, match="CRYPTOBOT_TESTNET_TOKEN"):
        settings.validate_runtime()


def test_testnet_token_blank_value_normalizes_to_none(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CRYPTOBOT_TESTNET_TOKEN=\n", encoding="utf-8")

    settings = load_settings(config_path=tmp_path / "missing.toml", env_file=env_file)

    assert settings.cryptobot_testnet_token is None


def _settings_with_test_tariff(*, test_mode: bool) -> Settings:
    return Settings(
        payments={"test_mode": test_mode},
        tariffs={
            "items": [
                {"code": "1m", "duration_months": 1, "base_price_usd": Decimal("79")},
                {
                    "code": "test",
                    "duration_months": 3,
                    "base_price_usd": Decimal("0.50"),
                    "test_only": True,
                },
            ]
        },
    )


def test_test_only_tariff_hidden_outside_test_mode() -> None:
    settings = _settings_with_test_tariff(test_mode=False)

    assert [tariff.code for tariff in settings.tariffs.items] == ["1m"]


def test_test_only_tariff_visible_in_test_mode() -> None:
    settings = _settings_with_test_tariff(test_mode=True)

    assert [tariff.code for tariff in settings.tariffs.items] == ["1m", "test"]
