from __future__ import annotations

import pytest

from bot import __main__ as main_module
from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import icons as icon_module
from bot.keyboards.menu import main_menu_keyboard, payment_keyboard, tariff_detail_keyboard
from bot.models import UserStatus
from bot.services.pricing import quote_tariff

ALL = set(icon_module.MENU_ICONS.values())


@pytest.fixture(autouse=True)
def _reset_icons():
    icon_module.apply_valid_icon_ids(ALL)
    yield
    icon_module.apply_valid_icon_ids(ALL)


def _by_callback(keyboard) -> dict[str, str | None]:
    return {
        button.callback_data: button.icon_custom_emoji_id
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def _by_url(keyboard) -> dict[str, str | None]:
    return {
        button.url: button.icon_custom_emoji_id
        for row in keyboard.inline_keyboard
        for button in row
        if button.url is not None
    }


def test_main_menu_buttons_carry_mapped_icons() -> None:
    settings = Settings()
    keyboard = main_menu_keyboard(JsonI18n(), "en", settings, is_admin=True)
    by_cb = _by_callback(keyboard)

    assert by_cb["buy"] == icon_module.MENU_ICONS["menu.button.buy"]
    assert by_cb["profile"] == icon_module.MENU_ICONS["menu.button.profile"]
    assert by_cb["language"] == icon_module.MENU_ICONS["menu.button.language"]
    assert by_cb["admin"] == icon_module.MENU_ICONS["menu.button.admin"]

    # Documentation + support are URL buttons (no callback_data); check their icons by URL.
    by_url = _by_url(keyboard)
    assert by_url[settings.bot.docs_url] == icon_module.MENU_ICONS["menu.button.docs"]
    assert (
        by_url[f"https://t.me/{settings.bot.support_username}"]
        == icon_module.MENU_ICONS["menu.button.support"]
    )


def test_pay_buttons_have_no_icons() -> None:
    settings = Settings(stars={"enabled": True}, xrocket={"enabled": True})
    keyboard = tariff_detail_keyboard(
        JsonI18n(), "1m", "en", settings, quote_tariff(settings, "1m", UserStatus.basic), 500
    )
    by_cb = _by_callback(keyboard)

    # Payment buttons carry the price in the label, no custom-emoji icon.
    assert by_cb["pay:cryptobot:1m"] is None
    assert by_cb["pay:stars:1m"] is None
    assert by_cb["pay:xrocket:1m"] is None


def test_payment_keyboard_pay_button_has_no_icon() -> None:
    keyboard = payment_keyboard(
        JsonI18n(),
        Settings(),
        "en",
        "https://t.me/xrocket?start=pay",
        "1m",
        payment_key="buy.pay.xrocket",
    )
    pay_button = keyboard.inline_keyboard[0][0]

    assert pay_button.icon_custom_emoji_id is None


def test_apply_valid_icon_ids_drops_unknown_ids() -> None:
    icon_module.apply_valid_icon_ids({icon_module.MENU_ICONS["menu.button.buy"]})
    by_cb = _by_callback(main_menu_keyboard(JsonI18n(), "en", Settings(), is_admin=False))

    assert by_cb["buy"] == icon_module.MENU_ICONS["menu.button.buy"]
    assert by_cb["profile"] is None  # not in the validated set -> no icon


def test_disable_icons_removes_all_icons() -> None:
    icon_module.disable_icons()
    keyboard = main_menu_keyboard(JsonI18n(), "en", Settings(), is_admin=True)
    icons = {button.icon_custom_emoji_id for row in keyboard.inline_keyboard for button in row}

    assert icons == {None}


def test_icon_for_returns_none_for_missing_or_none_key() -> None:
    assert icon_module.icon_for(None) is None
    assert icon_module.icon_for("does.not.exist") is None


class _FakeSticker:
    def __init__(self, custom_emoji_id: str) -> None:
        self.custom_emoji_id = custom_emoji_id


async def test_configure_menu_icons_keeps_only_valid_ids() -> None:
    valid_id = icon_module.MENU_ICONS["menu.button.buy"]

    class FakeBot:
        async def get_custom_emoji_stickers(self, ids):
            assert valid_id in ids
            return [_FakeSticker(valid_id)]

    await main_module.configure_menu_icons(FakeBot(), Settings())

    assert icon_module.icon_for("menu.button.buy") == valid_id
    assert icon_module.icon_for("menu.button.profile") is None


async def test_configure_menu_icons_disables_when_flag_off() -> None:
    class FakeBot:
        async def get_custom_emoji_stickers(self, ids):
            raise AssertionError("must not validate when icons are disabled")

    await main_module.configure_menu_icons(FakeBot(), Settings(bot={"custom_emoji_icons": False}))

    assert icon_module.icon_for("menu.button.buy") is None


async def test_configure_menu_icons_disables_on_api_error() -> None:
    class FakeBot:
        async def get_custom_emoji_stickers(self, ids):
            raise RuntimeError("telegram unavailable")

    await main_module.configure_menu_icons(FakeBot(), Settings())

    assert icon_module.icon_for("menu.button.buy") is None
