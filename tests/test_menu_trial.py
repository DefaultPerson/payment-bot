from __future__ import annotations

from bot.core.i18n import JsonI18n
from bot.keyboards import icons as icon_module
from bot.keyboards.menu import (
    CB_TRIAL_CANCEL,
    CB_TRIAL_CONFIRM,
    CB_TRIAL_START,
    main_menu_keyboard,
    trial_confirm_keyboard,
)


def _callbacks(keyboard) -> set[str]:
    return {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def test_main_menu_hides_trial_button_by_default() -> None:
    keyboard = main_menu_keyboard(JsonI18n(), "en", is_admin=False)

    assert CB_TRIAL_START not in _callbacks(keyboard)


def test_main_menu_shows_trial_button_when_available() -> None:
    keyboard = main_menu_keyboard(JsonI18n(), "en", is_admin=False, show_trial=True)

    assert CB_TRIAL_START in _callbacks(keyboard)


def test_main_menu_trial_button_carries_mapped_icon() -> None:
    keyboard = main_menu_keyboard(JsonI18n(), "en", is_admin=False, show_trial=True)
    by_cb = {
        button.callback_data: button.icon_custom_emoji_id
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert by_cb[CB_TRIAL_START] == icon_module.MENU_ICONS["menu.button.trial"]


def test_trial_confirm_keyboard_has_confirm_and_cancel() -> None:
    keyboard = trial_confirm_keyboard(JsonI18n(), "en")

    assert _callbacks(keyboard) == {CB_TRIAL_CONFIRM, CB_TRIAL_CANCEL}
