from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Settings
from bot.core.i18n import JsonI18n

CB_PROFILE = "profile"
CB_LANGUAGE = "language"
CB_SUPPORT = "support"
CB_BUY = "buy"
CB_REFERRAL = "referral"
CB_PROMO = "promo"
CB_RENEW = "renew"
CB_ENTER_CHAT = "enter_chat"
CB_ADMIN = "admin"
LANG_PREFIX = "lang:"

LANGUAGE_CODES = ("ru", "ua", "en")


def main_menu_keyboard(i18n: JsonI18n, lang_code: str, *, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.buy", lang_code),
                callback_data=CB_BUY,
            )
        ],
        [
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.profile", lang_code),
                callback_data=CB_PROFILE,
            ),
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.language", lang_code),
                callback_data=CB_LANGUAGE,
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.referral", lang_code),
                callback_data=CB_REFERRAL,
            ),
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.promo", lang_code),
                callback_data=CB_PROMO,
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n.gettext("menu.button.support", lang_code),
                callback_data=CB_SUPPORT,
            )
        ],
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=i18n.gettext("menu.button.admin", lang_code),
                    callback_data=CB_ADMIN,
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_keyboard(i18n: JsonI18n, lang_code: str) -> InlineKeyboardMarkup:
    selected_language = i18n.resolve_language(lang_code)
    rows = []
    for language_code in LANGUAGE_CODES:
        language_name = i18n.gettext(f"language.name.{language_code}", lang_code)
        if language_code == selected_language:
            language_name = i18n.gettext(
                "language.selected",
                lang_code,
                language_name=language_name,
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=language_name,
                    callback_data=f"{LANG_PREFIX}{language_code}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n.gettext("support.button", lang_code),
                    url=_support_url(settings),
                )
            ]
        ]
    )


def profile_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n.gettext("profile.button.renew", lang_code),
                    callback_data=CB_RENEW,
                )
            ],
            [
                InlineKeyboardButton(
                    text=i18n.gettext("profile.button.enter_chat", lang_code),
                    callback_data=CB_ENTER_CHAT,
                )
            ],
            [
                InlineKeyboardButton(
                    text=i18n.gettext("profile.button.support", lang_code),
                    url=_support_url(settings),
                )
            ],
        ]
    )


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"
