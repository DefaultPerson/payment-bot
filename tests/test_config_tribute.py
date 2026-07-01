from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bot.config import Settings, load_settings


def _runtime_settings(**tribute: Any) -> Settings:
    return Settings(bot_token="real-bot-token", service_channel_id=100, tribute=tribute)


def test_tribute_defaults_are_disabled_and_empty() -> None:
    settings = Settings()

    assert settings.tribute.enabled is False
    assert settings.tribute.token is None
    assert settings.tribute.webhook_path == "/webhooks/tribute"
    assert settings.tribute.products == []


def test_tribute_product_mapping_helpers() -> None:
    settings = Settings(
        tribute={
            "products": [
                {"product_id": 999, "tariff_code": "1m", "payment_link": "https://t.me/a"},
                {"product_id": 111, "tariff_code": "test", "payment_link": "https://t.me/b"},
            ],
        },
    )

    assert settings.tribute.tariff_for_product(999) == "1m"
    assert settings.tribute.tariff_for_product(111) == "test"
    assert settings.tribute.tariff_for_product(404) is None
    assert settings.tribute.link_for_tariff("test") == "https://t.me/b"
    assert settings.tribute.link_for_tariff("6m") is None


def test_validate_runtime_accepts_enabled_tribute_with_token_and_products() -> None:
    settings = _runtime_settings(
        enabled=True,
        token="tribute-key",
        products=[{"product_id": 1, "tariff_code": "1m", "payment_link": "https://t.me/a"}],
    )

    settings.validate_runtime()  # must not raise
    assert settings.tribute.token is not None
    assert settings.tribute.token.get_secret_value() == "tribute-key"


def test_validate_runtime_requires_token_when_enabled() -> None:
    settings = _runtime_settings(
        enabled=True,
        products=[{"product_id": 1, "tariff_code": "1m", "payment_link": "https://t.me/a"}],
    )

    with pytest.raises(ValueError, match="TRIBUTE_API_KEY must be set"):
        settings.validate_runtime()


def test_validate_runtime_requires_products_when_enabled() -> None:
    settings = _runtime_settings(enabled=True, token="tribute-key", products=[])

    with pytest.raises(ValueError, match="tribute.products must not be empty"):
        settings.validate_runtime()


def test_blank_tribute_token_normalizes_to_none() -> None:
    settings = Settings(tribute={"token": "  "})
    assert settings.tribute.token is None


def test_tribute_config_loads_from_toml_and_env(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [tribute]
        enabled = true
        webhook_path = "/hooks/tribute"

        [[tribute.products]]
        product_id = 4242
        tariff_code = "1m"
        payment_link = "https://t.me/tribute/app?startapp=abc"
        """,
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TRIBUTE_API_KEY=env-tribute-key\n", encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_file)

    assert settings.tribute.enabled is True
    assert settings.tribute.webhook_path == "/hooks/tribute"
    assert settings.tribute.token is not None
    assert settings.tribute.token.get_secret_value() == "env-tribute-key"
    assert settings.tribute.tariff_for_product(4242) == "1m"
