from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.buy import _duration_menu_text, _edit_text_or_answer
from bot.handlers.start import message_context
from bot.keyboards import CB_PROMO, CB_PROMO_BUY_PREFIX, CB_PROMO_CANCEL, tariff_keyboard
from bot.repos.promos import PromoUnavailable, apply_promo_code_for_user

logger = logging.getLogger(__name__)


class BuyPromoStates(StatesGroup):
    """Inline promo entry started from the duration page (Menu 2)."""

    waiting_code = State()


def parse_promo_args(text: str | None) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError
    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/promo":
        raise ValueError
    code = parts[1].strip()
    if not code:
        raise ValueError
    return code


async def promo_command(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del settings
    context = message_context(message)
    lang_code = i18n.resolve_language(context.language_code)
    if context.tg_id is None:
        await message.answer(i18n.gettext("error.generic", lang_code))
        return

    try:
        code = parse_promo_args(message.text)
    except ValueError:
        await message.answer(i18n.gettext("promo.usage", lang_code))
        return

    try:
        async with session_scope(session_factory) as session:
            promo = await apply_promo_code_for_user(
                session,
                tg_id=context.tg_id,
                code=code,
                language_code=context.language_code,
                i18n=i18n,
            )
            applied_code = promo.code
        await message.answer(i18n.gettext("promo.applied", lang_code, code=applied_code))
    except PromoUnavailable as exc:
        await message.answer(_promo_error_text(i18n, lang_code, exc.reason))
    except SQLAlchemyError:
        logger.exception("failed to apply promo code")
        await message.answer(i18n.gettext("error.generic", lang_code))


async def promo_callback(callback: CallbackQuery, i18n: JsonI18n) -> None:
    from_user = getattr(callback, "from_user", None)
    lang_code = i18n.resolve_language(getattr(from_user, "language_code", None))
    await callback.answer(i18n.gettext("promo.prompt", lang_code), show_alert=True)


async def promo_buy_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: JsonI18n,
) -> None:
    """Promo button on the duration page — prompt for a code via FSM."""
    del settings
    data = getattr(callback, "data", None)
    from_user = getattr(callback, "from_user", None)
    lang_code = i18n.resolve_language(getattr(from_user, "language_code", None))
    plan_code = (
        data.removeprefix(CB_PROMO_BUY_PREFIX)
        if isinstance(data, str) and data.startswith(CB_PROMO_BUY_PREFIX)
        else ""
    ).strip()
    if not plan_code:
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    await state.set_state(BuyPromoStates.waiting_code)
    await state.update_data(
        plan_code=plan_code,
        chat_id=getattr(chat, "id", None),
        message_id=getattr(message, "message_id", None),
    )
    await _edit_text_or_answer(
        callback,
        i18n.gettext("promo.enter", lang_code),
        reply_markup=_promo_cancel_keyboard(i18n, lang_code),
    )


async def promo_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: JsonI18n,
) -> None:
    """Abort inline promo entry and return to the duration page."""
    state_data = await state.get_data()
    plan_code = state_data.get("plan_code") or settings.tariffs.default_plan_code
    await state.clear()
    from_user = getattr(callback, "from_user", None)
    lang_code = i18n.resolve_language(getattr(from_user, "language_code", None))
    await _edit_text_or_answer(
        callback,
        _duration_menu_text(i18n, settings, lang_code, plan_code),
        reply_markup=tariff_keyboard(i18n, settings, lang_code, plan_code),
    )


async def promo_code_message(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Apply the typed promo code and re-render the duration page with a banner."""
    state_data = await state.get_data()
    plan_code = state_data.get("plan_code") or settings.tariffs.default_plan_code
    chat_id = state_data.get("chat_id")
    message_id = state_data.get("message_id")
    await state.clear()

    context = message_context(message)
    lang_code = i18n.resolve_language(context.language_code)
    code = (message.text or "").strip()

    if context.tg_id is None or not code:
        banner = i18n.gettext("promo.usage", lang_code)
    else:
        banner = await _apply_inline_promo(
            session_factory,
            i18n,
            lang_code,
            tg_id=context.tg_id,
            language_code=context.language_code,
            code=code,
        )

    await _render_duration_after_promo(
        message,
        settings,
        i18n,
        lang_code,
        plan_code,
        banner,
        chat_id=chat_id,
        message_id=message_id,
    )


async def _apply_inline_promo(
    session_factory: async_sessionmaker[AsyncSession],
    i18n: JsonI18n,
    lang_code: str,
    *,
    tg_id: int,
    language_code: str | None,
    code: str,
) -> str:
    try:
        async with session_scope(session_factory) as session:
            promo = await apply_promo_code_for_user(
                session,
                tg_id=tg_id,
                code=code,
                language_code=language_code,
                i18n=i18n,
            )
            applied_code = promo.code
        return i18n.gettext("promo.applied", lang_code, code=applied_code)
    except PromoUnavailable as exc:
        return _promo_error_text(i18n, lang_code, exc.reason)
    except SQLAlchemyError:
        logger.exception("failed to apply promo code")
        return i18n.gettext("error.generic", lang_code)


async def _render_duration_after_promo(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    lang_code: str,
    plan_code: str,
    banner: str,
    *,
    chat_id: int | None,
    message_id: int | None,
) -> None:
    text = f"{banner}\n\n{_duration_menu_text(i18n, settings, lang_code, plan_code)}"
    keyboard = tariff_keyboard(i18n, settings, lang_code, plan_code)

    bot = getattr(message, "bot", None)
    if bot is not None and chat_id is not None and message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
            )
        except Exception:
            logger.debug("could not edit promo prompt message", exc_info=True)
        else:
            await _delete_message_safe(message)
            return

    await message.answer(text, reply_markup=keyboard)
    await _delete_message_safe(message)


async def _delete_message_safe(message: Message) -> None:
    delete = getattr(message, "delete", None)
    if not callable(delete):
        return
    try:
        await delete()
    except Exception:
        logger.debug("could not delete promo code message", exc_info=True)


def _promo_cancel_keyboard(i18n: JsonI18n, lang_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n.gettext("promo.cancel", lang_code),
                    callback_data=CB_PROMO_CANCEL,
                )
            ]
        ]
    )


def _promo_error_text(i18n: JsonI18n, lang_code: str, reason: str) -> str:
    key = f"promo.error.{reason}"
    text = i18n.gettext(key, lang_code)
    if text == key:
        return i18n.gettext("promo.error.generic", lang_code)
    return text


def create_promo_router() -> Router:
    router = Router(name="promo")
    router.callback_query(F.data.startswith(CB_PROMO_BUY_PREFIX))(promo_buy_callback)
    router.callback_query(F.data == CB_PROMO_CANCEL)(promo_cancel_callback)
    # State-gated text capture must precede the /promo command so an in-flow message is
    # consumed by the FSM handler even if it happens to start with "/promo".
    router.message(StateFilter(BuyPromoStates.waiting_code))(promo_code_message)
    router.message(Command("promo"))(promo_command)
    router.callback_query(F.data == CB_PROMO)(promo_callback)
    return router
