from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards.icons import icon_for
from bot.models import UserStatus
from bot.repos.tariffs import get_active_plans, get_plan_tariffs, resolve_plan_code
from bot.services.pricing import quote_tariff

CB_PROFILE = "profile"
CB_LANGUAGE = "language"
CB_SUPPORT = "support"
CB_BUY = "buy"
CB_REFERRAL = "referral"
CB_PROMO = "promo"
CB_RENEW = "renew"
CB_ENTER_CHAT = "enter_chat"
CB_ADMIN = "admin"
CB_MENU = "menu"
LANG_PREFIX = "lang:"
CB_PLAN_PREFIX = "plan:"
CB_TARIFF_PREFIX = "tariff:"
CB_CRYPTOBOT_PREFIX = "pay:cryptobot:"
CB_STARS_PREFIX = "pay:stars:"
CB_XROCKET_PREFIX = "pay:xrocket:"
CB_TRIBUTE_PREFIX = "pay:tribute:"
CB_REFERRAL_PAY_PREFIX = "pay:referral:"
CB_PROMO_BUY_PREFIX = "promo_buy:"
CB_PROMO_CANCEL = "promo_cancel"
CB_TRIAL_START = "trial:start"
CB_TRIAL_CONFIRM = "trial:confirm"
CB_TRIAL_CANCEL = "trial:cancel"
CB_BACK_TARIFFS = "back:tariffs"
CB_BACK_DURATION_PREFIX = "back:dur:"

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


def main_menu_keyboard(
    i18n: JsonI18n,
    lang_code: str,
    settings: Settings,
    *,
    is_admin: bool,
    show_trial: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            _button(
                i18n.gettext("menu.button.buy", lang_code),
                icon_key="menu.button.buy",
                callback_data=CB_BUY,
            )
        ],
    ]
    if show_trial:
        rows.append(
            [
                _button(
                    i18n.gettext("menu.button.trial", lang_code),
                    icon_key="menu.button.trial",
                    callback_data=CB_TRIAL_START,
                )
            ]
        )
    rows += [
        # Documentation + support: two direct URL buttons on one row.
        [
            _button(
                i18n.gettext("menu.button.docs", lang_code),
                icon_key="menu.button.docs",
                url=_docs_url(settings),
            ),
            _button(
                i18n.gettext("menu.button.support", lang_code),
                icon_key="menu.button.support",
                url=_support_url(settings),
            ),
        ],
        [
            _button(
                i18n.gettext("menu.button.language", lang_code),
                icon_key="menu.button.language",
                callback_data=CB_LANGUAGE,
            ),
            _button(
                i18n.gettext("menu.button.profile", lang_code),
                icon_key="menu.button.profile",
                callback_data=CB_PROFILE,
            ),
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


def trial_confirm_keyboard(i18n: JsonI18n, lang_code: str) -> InlineKeyboardMarkup:
    """Confirm/cancel dialog shown before starting the one-time free trial."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    i18n.gettext("trial.confirm.button", lang_code),
                    icon_key="menu.button.trial",
                    callback_data=CB_TRIAL_CONFIRM,
                )
            ],
            [
                _button(
                    i18n.gettext("trial.cancel.button", lang_code),
                    callback_data=CB_TRIAL_CANCEL,
                )
            ],
        ]
    )


def language_keyboard(i18n: JsonI18n, lang_code: str) -> InlineKeyboardMarkup:
    selected_language = i18n.resolve_language(lang_code)
    rows = []
    for language_code in LANGUAGE_CODES:
        language_name = i18n.gettext(f"language.name.{language_code}", lang_code)
        # Mark the active language with a custom-emoji checkmark icon (not a trailing word).
        is_selected = language_code == selected_language
        rows.append(
            [
                _button(
                    language_name,
                    icon_key="language.check" if is_selected else None,
                    callback_data=f"{LANG_PREFIX}{language_code}",
                )
            ]
        )
    rows.append(
        [
            _button(
                i18n.gettext("menu.back", lang_code),
                callback_data=CB_MENU,
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


def plan_keyboard(i18n: JsonI18n, settings: Settings, lang_code: str) -> InlineKeyboardMarkup:
    """Menu 1 — tariff (product) selection. Shown even when a single plan is active so the
    flow stays a distinct step and a second plan just appears as another button."""
    rows = [
        [
            _button(
                plan.title or plan.code,
                icon_key="menu.button.buy",
                callback_data=f"{CB_PLAN_PREFIX}{plan.code}",
            )
        ]
        for plan in get_active_plans(settings)
    ]
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


def tariff_keyboard(
    i18n: JsonI18n,
    settings: Settings,
    lang_code: str,
    plan_code: str | None = None,
) -> InlineKeyboardMarkup:
    """Menu 2 — duration selection within a plan, with per-duration discount + price, a
    promo-code entry, and a back button to the tariff list."""
    plan_code = plan_code or settings.tariffs.default_plan_code
    rows = []
    for tariff in get_plan_tariffs(settings, plan_code):
        net_usd = quote_tariff(settings, tariff.code, UserStatus.basic).net_usd
        discount = tariff.duration_discount_percent
        if discount > 0:
            text = i18n.gettext(
                "buy.duration.button_discount",
                lang_code,
                months=tariff.duration_months,
                price=f"{net_usd:.2f}",
                discount=f"{discount:.0f}",
            )
        else:
            text = i18n.gettext(
                "buy.duration.button",
                lang_code,
                months=tariff.duration_months,
                price=f"{net_usd:.2f}",
            )
        rows.append(
            [
                _button(
                    text,
                    icon_key="buy.tariff.button",
                    callback_data=f"{CB_TARIFF_PREFIX}{tariff.code}",
                )
            ]
        )

    rows.append(
        [
            _button(
                i18n.gettext("buy.promo.button", lang_code),
                icon_key="menu.button.promo",
                callback_data=f"{CB_PROMO_BUY_PREFIX}{plan_code}",
            )
        ]
    )
    rows.append(
        [
            _button(
                i18n.gettext("buy.back", lang_code),
                callback_data=CB_BACK_TARIFFS,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariff_detail_keyboard(
    i18n: JsonI18n,
    tariff_code: str,
    lang_code: str,
    settings: Settings,
    *,
    show_referral_pay: bool = False,
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
    if settings.tribute.enabled and settings.tribute.link_for_tariff(tariff_code) is not None:
        rows.append(
            [
                _button(
                    i18n.gettext("buy.pay.tribute", lang_code),
                    icon_key="buy.pay.tribute",
                    callback_data=f"{CB_TRIBUTE_PREFIX}{tariff_code}",
                )
            ]
        )
    # Shown only when the caller has verified the user's referral balance covers the price
    # (see buy.tariff_callback); the handler re-checks under a row lock before granting.
    if show_referral_pay:
        rows.append(
            [
                _button(
                    i18n.gettext("buy.pay.referral_balance", lang_code),
                    icon_key="buy.pay.referral_balance",
                    callback_data=f"{CB_REFERRAL_PAY_PREFIX}{tariff_code}",
                )
            ]
        )
    plan_code = resolve_plan_code(settings, tariff_code)
    back_callback = (
        f"{CB_BACK_DURATION_PREFIX}{plan_code}" if plan_code is not None else CB_BACK_TARIFFS
    )
    rows.append(
        [
            _button(
                i18n.gettext("buy.back", lang_code),
                callback_data=back_callback,
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


def _docs_url(settings: Settings) -> str:
    return settings.bot.docs_url
