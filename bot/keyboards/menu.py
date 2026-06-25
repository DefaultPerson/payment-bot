from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards.icons import icon_for

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
CB_TARIFF_PREFIX = "tariff:"
CB_CRYPTOBOT_PREFIX = "pay:cryptobot:"
CB_STARS_PREFIX = "pay:stars:"
CB_XROCKET_PREFIX = "pay:xrocket:"

LANGUAGE_CODES = ("ru", "ua", "en")


def _button(
    text: str,
    *,
    icon_key: str | None = None,
    callback_data: str | None = None,
    url: str | None = None,
) -> InlineKeyboardButton:
    """Build a button, attaching the configured custom-emoji icon for ``icon_key``."""
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
        icon_custom_emoji_id=icon_for(icon_key),
    )


def main_menu_keyboard(i18n: JsonI18n, lang_code: str, *, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            _button(
                i18n.gettext("menu.button.buy", lang_code),
                icon_key="menu.button.buy",
                callback_data=CB_BUY,
            )
        ],
        [
            _button(
                i18n.gettext("menu.button.profile", lang_code),
                icon_key="menu.button.profile",
                callback_data=CB_PROFILE,
            ),
            _button(
                i18n.gettext("menu.button.language", lang_code),
                icon_key="menu.button.language",
                callback_data=CB_LANGUAGE,
            ),
        ],
        [
            _button(
                i18n.gettext("menu.button.referral", lang_code),
                icon_key="menu.button.referral",
                callback_data=CB_REFERRAL,
            ),
            _button(
                i18n.gettext("menu.button.promo", lang_code),
                icon_key="menu.button.promo",
                callback_data=CB_PROMO,
            ),
        ],
        [
            _button(
                i18n.gettext("menu.button.support", lang_code),
                icon_key="menu.button.support",
                callback_data=CB_SUPPORT,
            )
        ],
    ]
    if is_admin:
        rows.append(
            [
                _button(
                    i18n.gettext("menu.button.admin", lang_code),
                    icon_key="menu.button.admin",
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
                _button(
                    language_name,
                    callback_data=f"{LANG_PREFIX}{language_code}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    i18n.gettext("support.button", lang_code),
                    icon_key="support.button",
                    url=_support_url(settings),
                )
            ]
        ]
    )


def profile_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    i18n.gettext("profile.button.renew", lang_code),
                    icon_key="profile.button.renew",
                    callback_data=CB_RENEW,
                )
            ],
            [
                _button(
                    i18n.gettext("profile.button.enter_chat", lang_code),
                    icon_key="profile.button.enter_chat",
                    callback_data=CB_ENTER_CHAT,
                )
            ],
            [
                _button(
                    i18n.gettext("profile.button.support", lang_code),
                    icon_key="profile.button.support",
                    url=_support_url(settings),
                )
            ],
        ]
    )


def tariff_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    rows = []
    for tariff in settings.tariffs.items:
        if not tariff.active:
            continue

        rows.append(
            [
                _button(
                    i18n.gettext(
                        "buy.tariff.button",
                        lang_code,
                        months=tariff.duration_months,
                        price=f"{tariff.base_price_usd:.2f}",
                    ),
                    icon_key="buy.tariff.button",
                    callback_data=f"{CB_TARIFF_PREFIX}{tariff.code}",
                )
            ]
        )

    rows.append(
        [
            _button(
                i18n.gettext("support.button", lang_code),
                icon_key="support.button",
                url=_support_url(settings),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariff_detail_keyboard(
    i18n: JsonI18n,
    tariff_code: str,
    lang_code: str,
    settings: Settings,
) -> InlineKeyboardMarkup:
    rows = [
        [
            _button(
                i18n.gettext("buy.pay.cryptobot", lang_code),
                icon_key="buy.pay.cryptobot",
                callback_data=f"{CB_CRYPTOBOT_PREFIX}{tariff_code}",
            )
        ]
    ]
    if settings.stars.enabled:
        rows.append(
            [
                _button(
                    i18n.gettext("buy.pay.stars", lang_code),
                    icon_key="buy.pay.stars",
                    callback_data=f"{CB_STARS_PREFIX}{tariff_code}",
                )
            ]
        )
    if settings.xrocket.enabled:
        rows.append(
            [
                _button(
                    i18n.gettext("buy.pay.xrocket", lang_code),
                    icon_key="buy.pay.xrocket",
                    callback_data=f"{CB_XROCKET_PREFIX}{tariff_code}",
                )
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def payment_keyboard(
    i18n: JsonI18n,
    settings: Settings,
    lang_code: str,
    pay_url: str,
    *,
    payment_key: str = "buy.pay.cryptobot",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    i18n.gettext(payment_key, lang_code),
                    icon_key=payment_key,
                    url=pay_url,
                )
            ],
            [
                _button(
                    i18n.gettext("support.button", lang_code),
                    icon_key="support.button",
                    url=_support_url(settings),
                )
            ],
        ]
    )


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"
