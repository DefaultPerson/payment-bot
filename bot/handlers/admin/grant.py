from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.service_events import render_event, user_mention
from bot.handlers.admin._nav import callback_language, edit_or_answer, message_language
from bot.handlers.admin.states import AdminGrantState
from bot.keyboards.admin import back_keyboard, cancel_keyboard
from bot.repos.subscriptions import extend_manual_subscription
from bot.repos.tariffs import default_plan
from bot.repos.users import ensure_minimal_user

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GrantResult:
    target_tg_id: int
    days: int
    expires_at: datetime
    target_username: str | None


def parse_grant_args(text: str) -> tuple[int, int]:
    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/grant":
        raise ValueError

    try:
        user_id = int(parts[1])
        days = int(parts[2])
    except ValueError as exc:
        raise ValueError from exc

    if user_id <= 0 or days <= 0:
        raise ValueError

    return user_id, days


async def perform_grant(
    *,
    target_tg_id: int,
    days: int,
    admin_id: int,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    service_channel: ServiceChannel,
) -> GrantResult:
    """Extend a user's subscription by ``days`` and notify the service channel.

    Shared core of the grant flow; raises ``SQLAlchemyError`` on DB failure for the caller
    to translate into a user-facing error.
    """
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(session, tg_id=target_tg_id, i18n=i18n)
        subscription = await extend_manual_subscription(
            session,
            user=user,
            days=days,
            now=now,
            loyalty=settings.loyalty,
            plan_code=default_plan(settings).code,
        )
        expires_at = subscription.expires_at
        target_username = user.username

    await _notify_service_channel(
        service_channel,
        admin_id=admin_id,
        target_tg_id=target_tg_id,
        target_username=target_username,
        days=days,
        expires_at=expires_at,
    )
    return GrantResult(
        target_tg_id=target_tg_id,
        days=days,
        expires_at=expires_at,
        target_username=target_username,
    )


async def grant_start(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    user = callback.from_user
    if user is None or user.id not in settings.admin_ids:
        await callback.answer()
        return
    lang = callback_language(callback, i18n)
    await state.set_state(AdminGrantState.waiting_input)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.grant.prompt", lang),
        reply_markup=cancel_keyboard(i18n, lang),
    )


async def grant_input(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    service_channel: ServiceChannel,
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    admin_id = message.from_user.id if message.from_user else None
    if admin_id is None:
        return

    try:
        target_tg_id, days = parse_grant_args(f"/grant {message.text or ''}")
    except ValueError:
        await message.answer(
            i18n.gettext("admin.grant.invalid", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return

    try:
        result = await perform_grant(
            target_tg_id=target_tg_id,
            days=days,
            admin_id=admin_id,
            settings=settings,
            i18n=i18n,
            session_factory=session_factory,
            service_channel=service_channel,
        )
    except SQLAlchemyError:
        logger.exception("failed to apply manual grant")
        await state.set_state(None)
        await message.answer(
            i18n.gettext("error.generic", lang),
            reply_markup=back_keyboard(i18n, lang),
        )
        return

    await state.set_state(None)
    await message.answer(
        i18n.gettext(
            "grant.success",
            lang,
            user_id=result.target_tg_id,
            days=result.days,
            expires_at=_format_expiry(result.expires_at),
        ),
        reply_markup=back_keyboard(i18n, lang),
    )


async def _notify_service_channel(
    service_channel: ServiceChannel,
    *,
    admin_id: int,
    target_tg_id: int,
    target_username: str | None = None,
    days: int,
    expires_at: datetime,
) -> None:
    text = render_event(
        glyph="fast_up",
        title="Admin manual grant",
        fields=[
            ("User", user_mention(target_tg_id, target_username)),
            ("Granted", f"<code>{days}</code> days"),
        ],
        extra={"admin_id": admin_id, "expires_at": _format_expiry(expires_at)},
    )
    try:
        await service_channel.send_html(text)
    except Exception:
        return


def _format_expiry(expires_at: datetime) -> str:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    expires_at = expires_at.astimezone(UTC)
    return expires_at.strftime("%Y-%m-%d %H:%M UTC")
