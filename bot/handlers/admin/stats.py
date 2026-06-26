"""Admin stats screen: period-switchable revenue / users / referrers snapshot."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.admin._nav import callback_language, edit_or_answer
from bot.keyboards.admin import PFX_ADM_STATS_PERIOD, stats_keyboard
from bot.services.stats import StatsPeriod, StatsSnapshot, build_stats_snapshot


async def open_stats(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _render(callback, i18n, session_factory, StatsPeriod.today)


async def stats_period(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    period = _parse_period(callback.data)
    if period is None:
        await callback.answer()
        return
    await _render(callback, i18n, session_factory, period)


async def _render(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    period: StatsPeriod,
) -> None:
    lang = callback_language(callback, i18n)
    async with session_scope(session_factory) as session:
        snapshot = await build_stats_snapshot(session, period=period, now=datetime.now(UTC))
    await edit_or_answer(
        callback,
        _format_stats(i18n, lang, snapshot),
        reply_markup=stats_keyboard(i18n, lang, active=snapshot.period),
    )


def _parse_period(data: str | None) -> StatsPeriod | None:
    if not data or not data.startswith(PFX_ADM_STATS_PERIOD):
        return None
    try:
        return StatsPeriod(data.removeprefix(PFX_ADM_STATS_PERIOD))
    except ValueError:
        return None


def _format_stats(i18n: JsonI18n, lang: str, s: StatsSnapshot) -> str:
    period_label = i18n.gettext(f"stats.period.{s.period.value}", lang)
    lines = [
        i18n.gettext("stats.title", lang, period=period_label),
        "",
        i18n.gettext(
            "stats.revenue",
            lang,
            gross=s.revenue.gross,
            net=s.revenue.net,
            fee=s.revenue.fee,
            paid_count=s.revenue.paid_count,
        ),
    ]
    if s.by_provider:
        lines.append("")
        lines.extend(
            i18n.gettext(
                "stats.by_provider.line",
                lang,
                provider=p.provider.value,
                gross=p.gross,
                count=p.paid_count,
            )
            for p in s.by_provider
        )
    lines.extend(
        [
            "",
            i18n.gettext(
                "stats.users",
                lang,
                new_users=s.new_users,
                active_subscriptions=s.active_subscriptions,
            ),
            i18n.gettext("stats.activity", lang, dau=s.dau, mau=s.mau),
            "",
            i18n.gettext("stats.top_referrers.header", lang),
        ]
    )
    if s.top_referrers:
        lines.extend(
            i18n.gettext(
                "stats.top_referrers.line",
                lang,
                rank=index,
                who=f"@{r.username}" if r.username else f"id:{r.tg_id}",
                earned=r.earned_usd,
                count=r.referral_count,
            )
            for index, r in enumerate(s.top_referrers, start=1)
        )
    else:
        lines.append(i18n.gettext("stats.top_referrers.empty", lang))
    return "\n".join(lines)
