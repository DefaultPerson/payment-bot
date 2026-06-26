from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_broadcast.ui import BroadcastUIManager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.repos.broadcasts import get_broadcast_targets

SEGMENT_CALLBACK_PREFIX = "bcseg:"
_SEGMENTS = ("all", "active", "inactive")


def _segment_keyboard(i18n: JsonI18n, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n.gettext(f"broadcast.segment.{segment}", language),
                    callback_data=f"{SEGMENT_CALLBACK_PREFIX}{segment}",
                )
            ]
            for segment in _SEGMENTS
        ]
    )


async def broadcast_entry(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
) -> None:
    """Admin-panel entry (``adm:bcast``): post a fresh segment-selection prompt.

    A new message (not an edit of the panel) is sent because the broadcast composer takes over
    and replaces messages of its own — the panel stays put.
    """
    user = callback.from_user
    language = i18n.resolve_language(user.language_code if user else None)
    if user is None or user.id not in settings.admin_ids:
        await callback.answer()
        return
    message = callback.message
    if message is None or getattr(message.chat, "type", None) != "private":
        await callback.answer(i18n.gettext("broadcast.private_only", language), show_alert=True)
        return
    await message.answer(
        i18n.gettext("broadcast.choose_segment", language),
        reply_markup=_segment_keyboard(i18n, language),
    )
    await callback.answer()


async def broadcast_segment_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_ui: BroadcastUIManager | None = None,
) -> None:
    """Resolve the chosen segment to subscriber ids and open the library broadcast menu.

    ``broadcast_ui`` is injected by ``BroadcastUIMiddleware`` for private chats; the manager
    stores the ids and the composed message is later copied to exactly this list.
    """
    user = callback.from_user
    language = i18n.resolve_language(user.language_code if user else None)
    if user is None or user.id not in settings.admin_ids:
        await callback.answer(i18n.gettext("broadcast.forbidden", language), show_alert=True)
        return
    if broadcast_ui is None:
        await callback.answer(i18n.gettext("broadcast.private_only", language), show_alert=True)
        return

    segment = _parse_segment(callback.data)
    if segment is None:
        await callback.answer()
        return

    async with session_scope(session_factory) as session:
        subscriber_ids = await get_broadcast_targets(
            session,
            segment=segment,
            now=datetime.now(UTC),
        )

    await broadcast_ui.open_menu(subscriber_ids)
    if callback.message is not None:
        with suppress(TelegramBadRequest):
            await callback.message.delete()
    await callback.answer()


def _parse_segment(data: str | None) -> str | None:
    if not data or not data.startswith(SEGMENT_CALLBACK_PREFIX):
        return None
    value = data.removeprefix(SEGMENT_CALLBACK_PREFIX)
    return value if value in _SEGMENTS else None
