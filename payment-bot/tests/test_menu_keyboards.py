from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from aiogram.types import BotCommandScopeChat

from bot.config import Settings
from bot.core.i18n import JsonI18n

PROFILE_DISPLAY_KEYS = {
    "profile.text",
    "profile.status.basic",
    "profile.status.bronze",
    "profile.status.silver",
    "profile.status.gold",
    "profile.subscription.active",
    "profile.subscription.inactive",
    "profile.expires_at.none",
    "profile.next_tier.none",
}

BUY_DISPLAY_KEYS = {
    "buy.title",
    "buy.tariff.button",
    "buy.quote.text",
    "buy.pay.cryptobot",
    "buy.invoice.created",
    "buy.invoice.failed",
}


def command_names(commands: list) -> set[str]:
    return {f"/{command.command}" for command in commands}


def keyboard_callback_data(keyboard) -> set[str]:
    return {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def keyboard_urls(keyboard) -> set[str]:
    return {
        button.url
        for row in keyboard.inline_keyboard
        for button in row
        if button.url is not None
    }


def keyboard_texts(keyboard) -> set[str]:
    return {button.text for row in keyboard.inline_keyboard for button in row}


def menu_module():
    try:
        return import_module("bot.core.menu")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing menu implementation: {exc}")


def keyboard_module():
    try:
        return import_module("bot.keyboards.menu")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing keyboard implementation: {exc}")


def test_build_commands_includes_user_commands() -> None:
    commands = menu_module().build_commands(JsonI18n(), "en", is_admin=False)

    assert {"/start", "/profile", "/language", "/support"} <= command_names(commands)
    assert "/grant" not in command_names(commands)
    assert "/broadcast" not in command_names(commands)


def test_build_commands_includes_admin_commands() -> None:
    commands = menu_module().build_commands(JsonI18n(), "en", is_admin=True)

    assert "/grant" in command_names(commands)
    assert "/broadcast" in command_names(commands)


async def test_set_user_commands_scopes_commands_to_chat() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def set_my_commands(self, commands, *, scope=None, language_code=None) -> None:
            self.calls.append(
                {
                    "commands": commands,
                    "scope": scope,
                    "language_code": language_code,
                }
            )

    bot = FakeBot()

    await menu_module().set_user_commands(
        bot,
        JsonI18n(),
        Settings(),
        chat_id=42,
        lang_code="en",
        is_admin=False,
    )

    assert len(bot.calls) == 1
    assert isinstance(bot.calls[0]["scope"], BotCommandScopeChat)
    assert bot.calls[0]["scope"].chat_id == 42
    assert bot.calls[0]["language_code"] == "en"


async def test_set_user_commands_maps_internal_ua_to_telegram_uk() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def set_my_commands(self, commands, *, scope=None, language_code=None) -> None:
            self.calls.append(
                {
                    "commands": commands,
                    "scope": scope,
                    "language_code": language_code,
                }
            )

    bot = FakeBot()

    await menu_module().set_user_commands(
        bot,
        JsonI18n(),
        Settings(),
        chat_id=42,
        lang_code="ua",
        is_admin=False,
    )

    assert len(bot.calls) == 1
    assert bot.calls[0]["language_code"] == "uk"


def test_main_menu_keyboard_contains_user_and_future_feature_buttons() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", is_admin=False)

    assert {
        keyboards.CB_SUPPORT,
        keyboards.CB_PROFILE,
        keyboards.CB_LANGUAGE,
        keyboards.CB_BUY,
        keyboards.CB_REFERRAL,
        keyboards.CB_PROMO,
    } <= keyboard_callback_data(keyboard)


def test_main_menu_keyboard_contains_admin_button_for_admins() -> None:
    keyboards = keyboard_module()

    user_keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", is_admin=False)
    admin_keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", is_admin=True)

    assert keyboards.CB_ADMIN not in keyboard_callback_data(user_keyboard)
    assert keyboards.CB_ADMIN in keyboard_callback_data(admin_keyboard)


def test_language_keyboard_offers_supported_locales() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.language_keyboard(JsonI18n(), "en")

    assert {
        f"{keyboards.LANG_PREFIX}ru",
        f"{keyboards.LANG_PREFIX}ua",
        f"{keyboards.LANG_PREFIX}en",
    } <= keyboard_callback_data(keyboard)


def test_support_keyboard_points_to_configured_support_username() -> None:
    settings = Settings(bot={"support_username": "HydraSupport"})

    keyboard = keyboard_module().support_keyboard(JsonI18n(), settings, "ua")

    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)
    assert "Написати в підтримку" in keyboard_texts(keyboard)


def test_profile_keyboard_contains_expected_actions_and_support_url() -> None:
    settings = Settings(bot={"support_username": "HydraSupport"})
    keyboards = keyboard_module()

    keyboard = keyboards.profile_keyboard(JsonI18n(), settings, "en")

    assert {keyboards.CB_RENEW, keyboards.CB_ENTER_CHAT} <= keyboard_callback_data(keyboard)
    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)


def test_tariff_keyboard_contains_active_tariffs_and_support_url() -> None:
    settings = Settings(
        bot={"support_username": "HydraSupport"},
        tariffs={
            "items": [
                {
                    "code": "active",
                    "duration_months": 1,
                    "base_price_usd": "10",
                    "active": True,
                },
                {
                    "code": "inactive",
                    "duration_months": 2,
                    "base_price_usd": "20",
                    "active": False,
                },
            ]
        },
    )
    keyboards = keyboard_module()

    keyboard = keyboards.tariff_keyboard(JsonI18n(), settings, "en")

    assert f"{keyboards.CB_TARIFF_PREFIX}active" in keyboard_callback_data(keyboard)
    assert f"{keyboards.CB_TARIFF_PREFIX}inactive" not in keyboard_callback_data(keyboard)
    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)


def test_tariff_detail_keyboard_contains_cryptobot_payment_action() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.tariff_detail_keyboard(JsonI18n(), "3m", "en", Settings())

    assert f"{keyboards.CB_CRYPTOBOT_PREFIX}3m" in keyboard_callback_data(keyboard)
    assert "Pay with CryptoBot" in keyboard_texts(keyboard)


def test_tariff_detail_keyboard_contains_stars_payment_action_when_enabled() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.tariff_detail_keyboard(
        JsonI18n(),
        "1m",
        "en",
        Settings(stars={"enabled": True}),
    )

    assert f"{keyboards.CB_STARS_PREFIX}1m" in keyboard_callback_data(keyboard)
    assert "Pay with Telegram Stars" in keyboard_texts(keyboard)


def test_payment_keyboard_points_to_provider_url_and_support() -> None:
    keyboards = keyboard_module()
    settings = Settings(bot={"support_username": "HydraSupport"})

    keyboard = keyboards.payment_keyboard(
        JsonI18n(),
        settings,
        "en",
        pay_url="https://t.me/CryptoBot?start=pay",
    )

    assert "https://t.me/CryptoBot?start=pay" in keyboard_urls(keyboard)
    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)


def test_locale_files_include_profile_display_contract_keys() -> None:
    locales_dir = Path(__file__).resolve().parents[1] / "bot" / "locales"

    for locale_path in locales_dir.glob("*.json"):
        locale_data = json.loads(locale_path.read_text(encoding="utf-8"))

        assert PROFILE_DISPLAY_KEYS | BUY_DISPLAY_KEYS <= set(locale_data), locale_path.name



def test_profile_text_locale_formats_expected_kwargs() -> None:
    text = JsonI18n().gettext(
        "profile.text",
        "en",
        tg_id=42,
        status="Bronze",
        status_value="bronze",
        total_active_days=40,
        subscription_active=True,
        subscription_status="Active",
        expires_at="2026-07-01",
        days_to_next_tier=50,
        referral_count=3,
        referral_earned_usd="12.50",
        referral_unpaid_earned_usd="7.50",
    )

    assert "42" in text
    assert "Bronze" in text
    assert "40" in text
    assert "Active" in text
    assert "2026-07-01" in text
    assert "50" in text
    assert "3" in text
    assert "12.50" in text
    assert "7.50" in text
