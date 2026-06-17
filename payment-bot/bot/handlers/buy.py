from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.start import callback_context
from bot.keyboards import (
    CB_BUY,
    CB_CRYPTOBOT_PREFIX,
    CB_TARIFF_PREFIX,
    payment_keyboard,
    tariff_detail_keyboard,
    tariff_keyboard,
)
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotClient
from bot.repos.users import ensure_minimal_user
from bot.services.payments import create_cryptobot_invoice_for_user
from bot.services.pricing import TariffQuote, quote_tariff

logger = logging.getLogger(__name__)

CALLBACK_ANSWER_TEXT_LIMIT = 200


async def buy_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    try:
        lang_code = await _resolve_user_language(
            context.tg_id,
            context.language_code,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to resolve buy callback user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        i18n.gettext("buy.title", lang_code),
        reply_markup=tariff_keyboard(i18n, settings, lang_code),
    )


async def tariff_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_TARIFF_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return

    lang_code = i18n.resolve_language(context.language_code)
    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            quote = quote_tariff(settings, tariff_code, user.status)
            lang_code = user.lang_code
    except SQLAlchemyError:
        logger.exception("failed to resolve tariff callback user")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except LookupError:
        logger.info("invalid tariff callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        _format_quote_text(i18n, lang_code, quote),
        reply_markup=tariff_detail_keyboard(i18n, tariff_code, lang_code),
    )


async def cryptobot_invoice_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    cryptobot_client: CryptoBotClient | None,
) -> None:
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_CRYPTOBOT_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return
    if cryptobot_client is None:
        await callback.answer(
            i18n.gettext("buy.invoice.failed", context.language_code),
            show_alert=True,
        )
        return

    lang_code = i18n.resolve_language(context.language_code)
    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            created = await create_cryptobot_invoice_for_user(
                session,
                settings,
                cryptobot_client,
                user,
                tariff_code,
                _support_url(settings),
                datetime.now(UTC),
            )
            lang_code = user.lang_code
    except SQLAlchemyError:
        logger.exception("failed to create CryptoBot invoice")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except CryptoBotAPIError:
        logger.exception("CryptoBot invoice creation failed")
        await callback.answer(
            i18n.gettext("buy.invoice.failed", lang_code),
            show_alert=True,
        )
        return
    except LookupError:
        logger.info("invalid CryptoBot invoice callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        i18n.gettext("buy.invoice.created", lang_code),
        reply_markup=payment_keyboard(i18n, settings, lang_code, created.pay_url),
    )


async def _resolve_user_language(
    tg_id: int | None,
    language_code: str | None,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    if tg_id is None:
        return i18n.resolve_language(language_code)

    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(
            session,
            tg_id=tg_id,
            i18n=i18n,
            language_code=language_code,
        )
        return user.lang_code


def _format_quote_text(i18n: JsonI18n, lang_code: str, quote: TariffQuote) -> str:
    return i18n.gettext(
        "buy.quote.text",
        lang_code,
        months=quote.tariff.duration_months,
        gross_usd=f"{quote.gross_usd:.2f}",
        net_usd=f"{quote.net_usd:.2f}",
        fee_usd=f"{quote.fee_usd:.2f}",
    )


def _parse_prefixed_callback(data: str | None, prefix: str) -> str | None:
    if data is None or not data.startswith(prefix):
        return None
    value = data.removeprefix(prefix).strip()
    return value or None


async def _edit_text_or_answer(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: object | None = None,
) -> None:
    edit_text = getattr(callback.message, "edit_text", None)
    if callable(edit_text):
        try:
            await edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await callback.answer(_callback_answer_text(text), show_alert=True)
        else:
            await callback.answer()
        return

    await callback.answer(_callback_answer_text(text), show_alert=True)


def _callback_answer_text(text: str) -> str:
    if len(text) <= CALLBACK_ANSWER_TEXT_LIMIT:
        return text

    ellipsis = "..."
    return f"{text[: CALLBACK_ANSWER_TEXT_LIMIT - len(ellipsis)]}{ellipsis}"


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"


def create_buy_router() -> Router:
    router = Router(name="buy")
    router.callback_query(F.data == CB_BUY)(buy_callback)
    router.callback_query(F.data.startswith(CB_TARIFF_PREFIX))(tariff_callback)
    router.callback_query(F.data.startswith(CB_CRYPTOBOT_PREFIX))(cryptobot_invoice_callback)
    return router
