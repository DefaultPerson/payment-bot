"""Admin-panel root + navigation (open / cancel / close / noop)."""

from __future__ import annotations

from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.handlers.admin._nav import callback_language, edit_or_answer
from bot.keyboards.admin import root_keyboard


def _is_admin(callback: CallbackQuery, settings: Settings) -> bool:
    user = callback.from_user
    return user is not None and user.id in settings.admin_ids


async def open_panel(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    """Render the panel root. Also the ``adm:root`` / ``adm:cancel`` target — clears any
    half-finished admin FSM flow so the admin leaves it cleanly."""
    if not _is_admin(callback, settings):
        await callback.answer()
        return
    await state.set_state(None)
    lang = callback_language(callback, i18n)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.panel.title", lang),
        reply_markup=root_keyboard(i18n, lang),
    )


async def close_panel(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    if not _is_admin(callback, settings):
        await callback.answer()
        return
    await state.set_state(None)
    message = callback.message
    if message is not None:
        with suppress(TelegramBadRequest):
            await message.delete()
    await callback.answer()


async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
