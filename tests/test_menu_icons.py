from __future__ import annotations

import pytest

from bot import __main__ as main_module
from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import icons as icon_module
from bot.keyboards.menu import main_menu_keyboard, payment_keyboard, tariff_detail_keyboard

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


def test_main_menu_buttons_carry_mapped_icons() -> None:
    keyboard = main_menu_keyboard(JsonI18n(), "en", is_admin=True)
    by_cb = _by_callback(keyboard)

    assert by_cb["buy"] == icon_module.MENU_ICONS["menu.button.buy"]
    assert by_cb["profile"] == icon_module.MENU_ICONS["menu.button.profile"]
    assert by_cb["language"] == icon_module.MENU_ICONS["menu.button.language"]
    assert by_cb["referral"] == icon_module.MENU_ICONS["menu.button.referral"]
    assert by_cb["promo"] == icon_module.MENU_ICONS["menu.button.promo"]
    assert by_cb["support"] == icon_module.MENU_ICONS["menu.button.support"]
    assert by_cb["admin"] == icon_module.MENU_ICONS["menu.button.admin"]


def test_pay_buttons_get_provider_icons() -> None:
    keyboard = tariff_detail_keyboard(
        JsonI18n(),
        "1m",
        "en",
        Settings(stars={"enabled": True}, xrocket={"enabled": True}),
    )
    by_cb = _by_callback(keyboard)

    assert by_cb["pay:cryptobot:1m"] == icon_module.MENU_ICONS["buy.pay.cryptobot"]
    assert by_cb["pay:stars:1m"] == icon_module.MENU_ICONS["buy.pay.stars"]
    assert by_cb["pay:xrocket:1m"] == icon_module.MENU_ICONS["buy.pay.xrocket"]


def test_payment_keyboard_icon_follows_provider_key() -> None:
    keyboard = payment_keyboard(
        JsonI18n(),
        Settings(),
        "en",
        pay_url="https://t.me/xrocket?start=pay",
        payment_key="buy.pay.xrocket",
    )
    pay_button = keyboard.inline_keyboard[0][0]

    assert pay_button.icon_custom_emoji_id == icon_module.MENU_ICONS["buy.pay.xrocket"]


def test_apply_valid_icon_ids_drops_unknown_ids() -> None:
    icon_module.apply_valid_icon_ids({icon_module.MENU_ICONS["menu.button.buy"]})
    by_cb = _by_callback(main_menu_keyboard(JsonI18n(), "en", is_admin=False))

    assert by_cb["buy"] == icon_module.MENU_ICONS["menu.button.buy"]
    assert by_cb["profile"] is None  # not in the validated set -> no icon


def test_disable_icons_removes_all_icons() -> None:
    icon_module.disable_icons()
    keyboard = main_menu_keyboard(JsonI18n(), "en", is_admin=True)
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
