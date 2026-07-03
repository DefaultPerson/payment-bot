from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from bot import keyboards
from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import UserStatus
from bot.services.pricing import quote_tariff


def _quote(settings: Settings, code: str):
    return quote_tariff(settings, code, UserStatus.basic)


def _callbacks(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]


def _enabled_settings() -> Settings:
    return Settings(
        tribute={
            "enabled": True,
            "token": "tribute-key",
            "products": [
                {"product_id": 1, "tariff_code": "1m", "payment_link": "https://t.me/a"},
            ],
        },
    )


def test_tribute_button_hidden_when_disabled() -> None:
    settings = Settings()
    keyboard = keyboards.tariff_detail_keyboard(
        JsonI18n(), "1m", "en", settings, _quote(settings, "1m")
    )

    assert not any(c.startswith(keyboards.CB_TRIBUTE_PREFIX) for c in _callbacks(keyboard))


def test_tribute_button_shown_when_enabled_and_link_mapped() -> None:
    settings = _enabled_settings()
    keyboard = keyboards.tariff_detail_keyboard(
        JsonI18n(), "1m", "en", settings, _quote(settings, "1m")
    )

    assert f"{keyboards.CB_TRIBUTE_PREFIX}1m" in _callbacks(keyboard)


def test_tribute_button_hidden_for_tariff_without_a_mapped_product() -> None:
    settings = _enabled_settings()
    keyboard = keyboards.tariff_detail_keyboard(
        JsonI18n(), "6m", "en", settings, _quote(settings, "6m")
    )

    assert f"{keyboards.CB_TRIBUTE_PREFIX}6m" not in _callbacks(keyboard)
