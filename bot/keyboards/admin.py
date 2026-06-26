"""Inline keyboards + callback constants for the admin panel (``adm:`` namespace)."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import Promo, PromoKind
from bot.services.stats import StatsPeriod

# Fixed screens (exact F.data == ...).
CB_ADM_ROOT = "adm:root"
CB_ADM_CLOSE = "adm:close"
CB_ADM_GRANT = "adm:grant"
CB_ADM_REFSET = "adm:refset"
CB_ADM_PROMO = "adm:promo"
CB_ADM_PROMO_NEW = "adm:promo:new"
CB_ADM_PROMO_CONFIRM = "adm:promo:confirm"
CB_ADM_BCAST = "adm:bcast"
CB_ADM_STATS = "adm:stats"
CB_ADM_CANCEL = "adm:cancel"
CB_ADM_SKIP = "adm:skip"
CB_ADM_NOOP = "adm:noop"
CB_ADM_PROMO_TARIFF_DONE = "adm:promo:tariff:done"

# Parametrised screens (F.data.startswith(...)).
PFX_ADM_PROMO_PAGE = "adm:promo:page:"
PFX_ADM_PROMO_DEACT = "adm:promo:deact:"
PFX_ADM_PROMO_KIND = "adm:promo:kind:"
PFX_ADM_PROMO_TARIFF = "adm:promo:tariff:"
PFX_ADM_STATS_PERIOD = "adm:stats:p:"

PROMO_PAGE_SIZE = 5
_STATS_PERIODS = (StatsPeriod.today, StatsPeriod.d7, StatsPeriod.d30, StatsPeriod.all)


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _t(i18n: JsonI18n, lang: str, key: str) -> str:
    return i18n.gettext(key, lang)


def root_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(_t(i18n, lang, "admin.panel.button.grant"), CB_ADM_GRANT)],
            [_btn(_t(i18n, lang, "admin.panel.button.refset"), CB_ADM_REFSET)],
            [_btn(_t(i18n, lang, "admin.panel.button.promo"), CB_ADM_PROMO)],
            [_btn(_t(i18n, lang, "admin.panel.button.broadcast"), CB_ADM_BCAST)],
            [_btn(_t(i18n, lang, "admin.panel.button.stats"), CB_ADM_STATS)],
            [_btn(_t(i18n, lang, "admin.button.close"), CB_ADM_CLOSE)],
        ]
    )


def cancel_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(_t(i18n, lang, "admin.button.cancel"), CB_ADM_CANCEL)]],
    )


def skip_cancel_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(_t(i18n, lang, "admin.button.skip"), CB_ADM_SKIP)],
            [_btn(_t(i18n, lang, "admin.button.cancel"), CB_ADM_CANCEL)],
        ]
    )


def back_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(_t(i18n, lang, "admin.button.back"), CB_ADM_ROOT)]],
    )


def promo_list_keyboard(
    i18n: JsonI18n,
    lang: str,
    promos: Sequence[Promo],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(
                _t(i18n, lang, "admin.promo.button.deactivate") + f" {promo.code}",
                f"{PFX_ADM_PROMO_DEACT}{promo.id}",
            )
        ]
        for promo in promos
    ]
    if total_pages > 1:
        prev_cb = f"{PFX_ADM_PROMO_PAGE}{page - 1}" if page > 0 else CB_ADM_NOOP
        next_cb = f"{PFX_ADM_PROMO_PAGE}{page + 1}" if page < total_pages - 1 else CB_ADM_NOOP
        rows.append(
            [
                _btn("◀" if page > 0 else "·", prev_cb),
                _btn(f"{page + 1}/{total_pages}", CB_ADM_NOOP),
                _btn("▶" if page < total_pages - 1 else "·", next_cb),
            ]
        )
    rows.append([_btn(_t(i18n, lang, "admin.promo.button.new"), CB_ADM_PROMO_NEW)])
    rows.append([_btn(_t(i18n, lang, "admin.button.back"), CB_ADM_ROOT)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_kind_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(
                _t(i18n, lang, f"admin.promo.kind.{kind.value}"),
                f"{PFX_ADM_PROMO_KIND}{kind.value}",
            )
        ]
        for kind in PromoKind
    ]
    rows.append([_btn(_t(i18n, lang, "admin.button.cancel"), CB_ADM_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_tariffs_keyboard(
    i18n: JsonI18n,
    lang: str,
    settings: Settings,
    *,
    selected: Sequence[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for tariff in settings.tariffs.items:
        if not tariff.active:
            continue
        mark = "✅ " if tariff.code in selected else ""
        rows.append([_btn(f"{mark}{tariff.code}", f"{PFX_ADM_PROMO_TARIFF}{tariff.code}")])
    rows.append([_btn(_t(i18n, lang, "admin.promo.tariffs.done"), CB_ADM_PROMO_TARIFF_DONE)])
    rows.append([_btn(_t(i18n, lang, "admin.button.cancel"), CB_ADM_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_confirm_keyboard(i18n: JsonI18n, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(_t(i18n, lang, "admin.promo.button.confirm"), CB_ADM_PROMO_CONFIRM)],
            [_btn(_t(i18n, lang, "admin.button.cancel"), CB_ADM_CANCEL)],
        ]
    )


def stats_keyboard(i18n: JsonI18n, lang: str, *, active: StatsPeriod) -> InlineKeyboardMarkup:
    period_row = [
        _btn(
            ("• " if period is active else "") + _t(i18n, lang, f"stats.period.{period.value}"),
            f"{PFX_ADM_STATS_PERIOD}{period.value}",
        )
        for period in _STATS_PERIODS
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            period_row,
            [_btn(_t(i18n, lang, "admin.button.back"), CB_ADM_ROOT)],
        ]
    )
