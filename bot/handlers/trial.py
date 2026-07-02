from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.service_events import fmt_dt, render_event, user_mention
from bot.handlers.buy import _edit_text_or_answer
from bot.handlers.start import build_start_text, callback_context
from bot.keyboards import (
    CB_TRIAL_CANCEL,
    CB_TRIAL_CONFIRM,
    CB_TRIAL_START,
    main_menu_keyboard,
    trial_confirm_keyboard,
)
from bot.services.access import send_access_links
from bot.services.trial import TrialResult, activate_trial, trial_availability

logger = logging.getLogger(__name__)

_ALERT_KEYS: dict[str, str] = {
    "already_used": "trial.already_used",
    "has_active": "trial.has_active",
    "disabled": "trial.unavailable",
}


async def trial_start_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Trial button — re-validate eligibility, then show the confirmation dialog."""
    context = callback_context(callback, settings)
    lang_code = i18n.resolve_language(context.language_code)
    if not settings.trial.enabled or context.tg_id is None:
        await callback.answer(i18n.gettext("trial.unavailable", lang_code), show_alert=True)
        return

    try:
        available = await trial_availability(
            session_factory,
            settings,
            i18n,
            tg_id=context.tg_id,
            language_code=context.language_code,
            now=datetime.now(UTC),
        )
    except SQLAlchemyError:
        logger.exception("failed to check trial availability")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    if not available:
        await callback.answer(i18n.gettext("trial.unavailable", lang_code), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        i18n.gettext("trial.confirm.text", lang_code, days=settings.trial.duration_days),
        reply_markup=trial_confirm_keyboard(i18n, lang_code),
    )


async def trial_confirm_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    service_channel: ServiceChannel | None = None,
) -> None:
    """Grant the trial subscription (idempotent) and deliver access + notify the channel."""
    context = callback_context(callback, settings)
    lang_code = i18n.resolve_language(context.language_code)
    if context.tg_id is None:
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    now = datetime.now(UTC)
    try:
        result = await activate_trial(
            session_factory,
            settings,
            i18n,
            tg_id=context.tg_id,
            language_code=context.language_code,
            now=now,
        )
    except SQLAlchemyError:
        logger.exception("failed to activate trial")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    if result.status != "activated":
        await callback.answer(
            i18n.gettext(_ALERT_KEYS.get(result.status, "trial.unavailable"), lang_code),
            show_alert=True,
        )
        return

    lang_code = result.lang_code or lang_code
    if result.user_tg_id is None:
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    try:
        await send_access_links(
            bot,
            settings,
            i18n,
            tg_id=result.user_tg_id,
            lang_code=lang_code,
            now=now,
            plan_code=result.plan_code,
        )
    except Exception:
        logger.exception("failed to send trial access links", extra={"tg_id": result.user_tg_id})
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    if service_channel is not None:
        await _notify_trial_started(service_channel, result)

    await _edit_text_or_answer(
        callback,
        i18n.gettext(
            "trial.success.text",
            lang_code,
            days=result.duration_days,
            expires_at=fmt_dt(result.expires_at),
        ),
    )


async def trial_cancel_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
) -> None:
    """Abort the trial confirmation and return to the main menu (still eligible)."""
    context = callback_context(callback, settings)
    lang_code = i18n.resolve_language(context.language_code)
    # Reaching cancel means the trial was still available, so the user has no active
    # subscription — the welcome status is Free without needing a DB read.
    status_label = i18n.gettext("start.status.free", lang_code)
    await _edit_text_or_answer(
        callback,
        build_start_text(
            i18n, settings, lang_code, status_label=status_label, trial_available=True
        ),
        reply_markup=main_menu_keyboard(
            i18n, lang_code, settings, is_admin=context.is_admin, show_trial=True
        ),
    )


async def _notify_trial_started(service_channel: ServiceChannel, result: TrialResult) -> None:
    text = render_event(
        glyph="fast_up",
        title="Trial started",
        fields=[
            ("User", user_mention(result.user_tg_id, result.username)),
            ("Trial", f"<code>{result.duration_days}</code> days"),
        ],
        extra={
            "plan": result.plan_code or "—",
            "expires_at": fmt_dt(result.expires_at),
        },
    )
    try:
        await service_channel.send_html(text)
    except Exception:
        logger.exception("failed to send trial started event", extra={"tg_id": result.user_tg_id})


def create_trial_router() -> Router:
    router = Router(name="trial")
    router.callback_query(F.data == CB_TRIAL_START)(trial_start_callback)
    router.callback_query(F.data == CB_TRIAL_CONFIRM)(trial_confirm_callback)
    router.callback_query(F.data == CB_TRIAL_CANCEL)(trial_cancel_callback)
    return router
