"""Shared navigation primitive for the admin panel: edit the current message in place.

Every panel screen re-renders by editing the same message. ``edit_or_answer`` mirrors the
support-handler pattern but additionally swallows Telegram's "message is not modified" error
(common when re-rendering an identical screen) as a silent ``callback.answer()``.
"""

from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.core.i18n import JsonI18n


def callback_language(callback: CallbackQuery, i18n: JsonI18n) -> str:
    user = getattr(callback, "from_user", None)
    return i18n.resolve_language(getattr(user, "language_code", None))


def message_language(message: Message, i18n: JsonI18n) -> str:
    user = getattr(message, "from_user", None)
    return i18n.resolve_language(getattr(user, "language_code", None))


async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    edit_text = getattr(callback.message, "edit_text", None)
    if callable(edit_text):
        try:
            await edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                await callback.answer()
                return
            await callback.answer(text, show_alert=True)
        else:
            await callback.answer()
        return

    await callback.answer(text, show_alert=True)
