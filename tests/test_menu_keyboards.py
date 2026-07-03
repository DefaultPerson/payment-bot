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
    "buy.pay.xrocket",
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
        button.url for row in keyboard.inline_keyboard for button in row if button.url is not None
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
    names = command_names(commands)

    assert {"/start", "/language"} <= names
    # /profile and /support are reachable via menu buttons, not the command list
    assert "/profile" not in names
    assert "/support" not in names
    assert "/grant" not in names
    assert "/broadcast" not in names


def test_build_commands_excludes_admin_commands() -> None:
    # Admin actions moved to the inline admin panel; the slash-command menu has no admin entries.
    names = command_names(menu_module().build_commands(JsonI18n(), "en", is_admin=True))

    assert names == {"/start", "/language"}
    assert "/grant" not in names
    assert "/broadcast" not in names


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


def test_main_menu_keyboard_contains_user_buttons_and_link_row() -> None:
    keyboards = keyboard_module()
    settings = Settings(bot={"support_username": "HydraSupport"})

    keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", settings, is_admin=False)

    callbacks = keyboard_callback_data(keyboard)
    # Core callback buttons remain.
    assert {keyboards.CB_BUY, keyboards.CB_PROFILE, keyboards.CB_LANGUAGE} <= callbacks
    # Promo/referral buttons removed; support is now a direct URL button (no callback).
    assert keyboards.CB_PROMO not in callbacks
    assert keyboards.CB_REFERRAL not in callbacks
    assert keyboards.CB_SUPPORT not in callbacks
    # Documentation + support ship as URL buttons on one row.
    urls = keyboard_urls(keyboard)
    assert settings.bot.docs_url in urls
    assert "https://t.me/HydraSupport" in urls


def test_main_menu_keyboard_contains_admin_button_for_admins() -> None:
    keyboards = keyboard_module()

    user_keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", Settings(), is_admin=False)
    admin_keyboard = keyboards.main_menu_keyboard(JsonI18n(), "en", Settings(), is_admin=True)

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


def test_language_keyboard_has_back_button_and_checkmark_on_selected() -> None:
    from bot.keyboards import icons as icon_module

    keyboards = keyboard_module()

    keyboard = keyboards.language_keyboard(JsonI18n(), "en")

    # Back-to-menu button present.
    assert keyboards.CB_MENU in keyboard_callback_data(keyboard)
    buttons = {
        button.callback_data: button
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    # Selected language marked with a custom-emoji checkmark icon (not a word), label unchanged.
    selected = buttons[f"{keyboards.LANG_PREFIX}en"]
    assert selected.icon_custom_emoji_id == icon_module.MENU_ICONS["language.check"]
    assert selected.text == "English"
    assert "selected" not in selected.text.lower()
    # Non-selected languages carry no checkmark icon.
    assert buttons[f"{keyboards.LANG_PREFIX}ru"].icon_custom_emoji_id is None


def test_support_keyboard_points_to_configured_support_username() -> None:
    settings = Settings(bot={"support_username": "HydraSupport"})

    keyboard = keyboard_module().support_keyboard(JsonI18n(), settings, "ua")

    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)
    assert "Написати в підтримку" in keyboard_texts(keyboard)


def test_profile_keyboard_contains_expected_actions_and_support_url() -> None:
    settings = Settings(bot={"support_username": "HydraSupport"})
    keyboards = keyboard_module()

    keyboard = keyboards.profile_keyboard(JsonI18n(), settings, "en")

    callbacks = keyboard_callback_data(keyboard)
    assert keyboards.CB_ENTER_CHAT in callbacks
    assert keyboards.CB_MENU in callbacks  # back-to-menu button at the bottom
    assert keyboards.CB_RENEW not in callbacks  # broken "renew" button removed
    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)


def test_dismiss_keyboard_deletes_message_with_cross_icon() -> None:
    keyboards = keyboard_module()
    icon_module = import_module("bot.keyboards.icons")

    keyboard = keyboards.dismiss_keyboard(JsonI18n(), "en")

    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == keyboards.CB_DISMISS
    assert button.icon_custom_emoji_id == icon_module.MENU_ICONS["access.button.dismiss"]


def test_plan_keyboard_lists_active_plans_and_support_url() -> None:
    settings = Settings(
        bot={"support_username": "HydraSupport"},
        tariffs={
            "plans": [
                {"code": "main", "title": "Main plan"},
                {"code": "pro", "title": "Pro plan", "active": False},
            ],
            "items": [
                {
                    "code": "main_1m",
                    "plan_code": "main",
                    "duration_months": 1,
                    "base_price_usd": "59",
                }
            ],
        },
    )
    keyboards = keyboard_module()

    keyboard = keyboards.plan_keyboard(JsonI18n(), settings, "en")

    assert f"{keyboards.CB_PLAN_PREFIX}main" in keyboard_callback_data(keyboard)
    assert f"{keyboards.CB_PLAN_PREFIX}pro" not in keyboard_callback_data(keyboard)
    assert "Main plan" in keyboard_texts(keyboard)
    assert "https://t.me/HydraSupport" in keyboard_urls(keyboard)


def test_tariff_keyboard_lists_plan_durations_with_promo_and_back() -> None:
    settings = Settings(
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

    keyboard = keyboards.tariff_keyboard(JsonI18n(), settings, "en", "main")
    callbacks = keyboard_callback_data(keyboard)

    assert f"{keyboards.CB_TARIFF_PREFIX}active" in callbacks
    assert f"{keyboards.CB_TARIFF_PREFIX}inactive" not in callbacks
    # Promo entry and back navigation live on the duration page; support does not.
    assert f"{keyboards.CB_PROMO_BUY_PREFIX}main" in callbacks
    assert keyboards.CB_BACK_TARIFFS in callbacks
    assert "https://t.me/HydraSupport" not in keyboard_urls(keyboard)


def test_tariff_keyboard_labels_show_discount_and_net_price() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.tariff_keyboard(JsonI18n(), Settings(), "en", "main")
    texts = keyboard_texts(keyboard)

    # 1m has no discount ($59.00); 3m is -5% ($168.15); 6m is -10% ($318.60).
    assert any("$59.00" in text and "%" not in text for text in texts)
    assert any("$168.15" in text and "5%" in text for text in texts)
    assert any("$318.60" in text and "10%" in text for text in texts)


def test_tariff_detail_keyboard_has_back_to_duration() -> None:
    keyboards = keyboard_module()

    keyboard = keyboards.tariff_detail_keyboard(JsonI18n(), "3m", "en", Settings())

    assert f"{keyboards.CB_BACK_DURATION_PREFIX}main" in keyboard_callback_data(keyboard)


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


def test_tariff_detail_keyboard_contains_xrocket_payment_action_only_when_enabled() -> None:
    keyboards = keyboard_module()

    disabled = keyboards.tariff_detail_keyboard(JsonI18n(), "1m", "en", Settings())
    enabled = keyboards.tariff_detail_keyboard(
        JsonI18n(),
        "1m",
        "en",
        Settings(xrocket={"enabled": True}),
    )

    assert f"{keyboards.CB_XROCKET_PREFIX}1m" not in keyboard_callback_data(disabled)
    assert f"{keyboards.CB_XROCKET_PREFIX}1m" in keyboard_callback_data(enabled)
    assert "Pay with xRocket" in keyboard_texts(enabled)


def test_tariff_detail_keyboard_shows_referral_pay_only_when_flagged() -> None:
    keyboards = keyboard_module()

    hidden = keyboards.tariff_detail_keyboard(JsonI18n(), "1m", "en", Settings())
    shown = keyboards.tariff_detail_keyboard(
        JsonI18n(), "1m", "en", Settings(), show_referral_pay=True
    )

    assert f"{keyboards.CB_REFERRAL_PAY_PREFIX}1m" not in keyboard_callback_data(hidden)
    assert f"{keyboards.CB_REFERRAL_PAY_PREFIX}1m" in keyboard_callback_data(shown)
    assert "Pay with referral balance" in keyboard_texts(shown)


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


def test_payment_keyboard_can_use_xrocket_provider_label() -> None:
    keyboards = keyboard_module()
    settings = Settings(bot={"support_username": "HydraSupport"})

    keyboard = keyboards.payment_keyboard(
        JsonI18n(),
        settings,
        "en",
        pay_url="https://t.me/xrocket?start=pay",
        payment_key="buy.pay.xrocket",
    )

    assert "https://t.me/xrocket?start=pay" in keyboard_urls(keyboard)
    assert "Pay with xRocket" in keyboard_texts(keyboard)


def test_locale_files_include_profile_display_contract_keys() -> None:
    locales_dir = Path(__file__).resolve().parents[1] / "bot" / "locales"

    for locale_path in locales_dir.glob("*.json"):
        locale_data = json.loads(locale_path.read_text(encoding="utf-8"))

        assert PROFILE_DISPLAY_KEYS | BUY_DISPLAY_KEYS <= set(locale_data), locale_path.name


def test_profile_text_locale_formats_expected_kwargs() -> None:
    text = JsonI18n().gettext(
        "profile.text",
        "en",
        profile_emoji="👤",
        referral_emoji="👥",
        free_emoji="🆓",
        tg_id=42,
        plan="Free",
        status="Bronze",
        status_value="bronze",
        total_active_days=40,
        subscription_active=True,
        subscription_status="Active",
        expires_at="2026-07-01",
        days_to_next_tier=50,
        referral_count=3,
        referral_percent="10",
        referral_earned_usd="12.50",
        referral_unpaid_earned_usd="7.50",
        invite_link="https://t.me/HMPTbot?start=ref_42",
        min_withdraw_usd="10.00",
        coin_emoji="🪙",
    )

    assert "42" in text
    assert "Free" in text
    assert "Bronze" in text
    assert "40" in text
    assert "2026-07-01" in text
    assert "50" in text
    assert "3" in text
    assert "10%" in text
    assert "12.50" in text
    assert "7.50" in text
    assert "https://t.me/HMPTbot?start=ref_42" in text
    assert "10.00" in text
