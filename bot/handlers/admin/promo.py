"""Admin promo-code management: paginated list + deactivate, and a multi-step create FSM."""

from __future__ import annotations

import html
import logging
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.admin._nav import callback_language, edit_or_answer, message_language
from bot.handlers.admin.states import AdminPromoState
from bot.keyboards.admin import (
    PFX_ADM_PROMO_DEACT,
    PFX_ADM_PROMO_KIND,
    PFX_ADM_PROMO_PAGE,
    PFX_ADM_PROMO_TARIFF,
    PROMO_PAGE_SIZE,
    back_keyboard,
    cancel_keyboard,
    promo_confirm_keyboard,
    promo_kind_keyboard,
    promo_list_keyboard,
    promo_tariffs_keyboard,
    skip_cancel_keyboard,
)
from bot.models import Promo, PromoKind
from bot.repos.promos import (
    count_active_promos,
    create_promo,
    deactivate_promo,
    get_promo_by_code,
    list_active_promos,
    normalize_promo_code,
)
from bot.repos.users import ensure_minimal_user

logger = logging.getLogger(__name__)


# --- list / deactivate ----------------------------------------------------------------------


def _promo_line(p: Promo) -> str:
    parts = [f"<b>{html.escape(p.code)}</b>", html.escape(p.kind.value), f"{p.value}"]
    if p.max_uses is not None:
        parts.append(f"{p.uses}/{p.max_uses}")
    return " · ".join(parts)


async def _show_list(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    page: int,
    lang: str,
    toast: str = "",
) -> None:
    async with session_scope(session_factory) as session:
        total = await count_active_promos(session)
        total_pages = max(1, math.ceil(total / PROMO_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        promos = await list_active_promos(
            session,
            limit=PROMO_PAGE_SIZE,
            offset=page * PROMO_PAGE_SIZE,
        )

    if promos:
        lines = [i18n.gettext("admin.promo.title", lang), *(_promo_line(p) for p in promos)]
        text = "\n".join(lines)
    else:
        text = i18n.gettext("admin.promo.empty", lang)

    keyboard = promo_list_keyboard(i18n, lang, promos, page=page, total_pages=total_pages)
    edit = getattr(callback.message, "edit_text", None)
    if callable(edit):
        try:
            await edit(text, reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                await callback.answer(text, show_alert=True)
                return
    await callback.answer(toast)


async def promo_list(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    lang = callback_language(callback, i18n)
    await _show_list(callback, i18n, session_factory, page=0, lang=lang)


async def promo_page(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    page = _parse_int_suffix(callback.data, PFX_ADM_PROMO_PAGE)
    if page is None:
        await callback.answer()
        return
    lang = callback_language(callback, i18n)
    await _show_list(callback, i18n, session_factory, page=page, lang=lang)


async def promo_deactivate(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    promo_id = _parse_int_suffix(callback.data, PFX_ADM_PROMO_DEACT)
    if promo_id is None:
        await callback.answer()
        return
    lang = callback_language(callback, i18n)
    async with session_scope(session_factory) as session:
        ok = await deactivate_promo(session, promo_id)
    toast = i18n.gettext("admin.promo.deactivated" if ok else "admin.promo.deact_failed", lang)
    await _show_list(callback, i18n, session_factory, page=0, lang=lang, toast=toast)


# --- create FSM -----------------------------------------------------------------------------


async def promo_new_start(
    callback: CallbackQuery,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = callback_language(callback, i18n)
    await state.set_data({})
    await state.set_state(AdminPromoState.code)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.promo.new.code", lang),
        reply_markup=cancel_keyboard(i18n, lang),
    )


async def promo_code_input(
    message: Message,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    normalized = normalize_promo_code(message.text or "")
    if not normalized:
        await message.answer(
            i18n.gettext("admin.promo.new.code", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return
    async with session_scope(session_factory) as session:
        existing = await get_promo_by_code(session, normalized)
    if existing is not None:
        await message.answer(
            i18n.gettext("admin.promo.duplicate", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return
    await state.update_data(code=normalized)
    await state.set_state(AdminPromoState.kind)
    await message.answer(
        i18n.gettext("admin.promo.new.kind", lang),
        reply_markup=promo_kind_keyboard(i18n, lang),
    )


async def promo_kind_choice(
    callback: CallbackQuery,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    kind_value = _suffix(callback.data, PFX_ADM_PROMO_KIND)
    try:
        PromoKind(kind_value)
    except ValueError:
        await callback.answer()
        return
    lang = callback_language(callback, i18n)
    await state.update_data(kind=kind_value)
    await state.set_state(AdminPromoState.value)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.promo.new.value", lang),
        reply_markup=cancel_keyboard(i18n, lang),
    )


async def promo_value_input(
    message: Message,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    data = await state.get_data()
    value = _validate_value(PromoKind(data["kind"]), message.text or "")
    if value is None:
        await message.answer(
            i18n.gettext("admin.promo.new.value_invalid", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return
    await state.update_data(value=str(value))
    await state.set_state(AdminPromoState.max_uses)
    await message.answer(
        i18n.gettext("admin.promo.new.max_uses", lang),
        reply_markup=skip_cancel_keyboard(i18n, lang),
    )


async def promo_maxuses_input(
    message: Message,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    raw = (message.text or "").strip()
    try:
        max_uses = int(raw)
    except ValueError:
        max_uses = 0
    if max_uses <= 0:
        await message.answer(
            i18n.gettext("admin.promo.new.max_uses", lang),
            reply_markup=skip_cancel_keyboard(i18n, lang),
        )
        return
    await state.update_data(max_uses=max_uses)
    await state.set_state(AdminPromoState.expires_at)
    await message.answer(
        i18n.gettext("admin.promo.new.expires", lang),
        reply_markup=skip_cancel_keyboard(i18n, lang),
    )


async def promo_maxuses_skip(
    callback: CallbackQuery,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = callback_language(callback, i18n)
    await state.update_data(max_uses=None)
    await state.set_state(AdminPromoState.expires_at)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.promo.new.expires", lang),
        reply_markup=skip_cancel_keyboard(i18n, lang),
    )


async def promo_expires_input(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    try:
        expires_at = _parse_expires((message.text or "").strip())
    except ValueError:
        await message.answer(
            i18n.gettext("admin.promo.new.expires_invalid", lang),
            reply_markup=skip_cancel_keyboard(i18n, lang),
        )
        return
    await state.update_data(expires_at=expires_at.isoformat())
    text, keyboard = await _tariffs_view(i18n, settings, state, lang)
    await message.answer(text, reply_markup=keyboard)


async def promo_expires_skip(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = callback_language(callback, i18n)
    await state.update_data(expires_at=None)
    text, keyboard = await _tariffs_view(i18n, settings, state, lang)
    await edit_or_answer(callback, text, reply_markup=keyboard)


async def promo_tariff_toggle(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    code = _suffix(callback.data, PFX_ADM_PROMO_TARIFF)
    valid_codes = {tariff.code for tariff in settings.tariffs.items if tariff.active}
    if code is None or code not in valid_codes:
        await callback.answer()
        return
    data = await state.get_data()
    selected = list(data.get("allowed_tariffs") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    await state.update_data(allowed_tariffs=selected)
    lang = callback_language(callback, i18n)
    text, keyboard = await _tariffs_view(i18n, settings, state, lang)
    await edit_or_answer(callback, text, reply_markup=keyboard)


async def promo_tariffs_done(
    callback: CallbackQuery,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    lang = callback_language(callback, i18n)
    await state.set_state(AdminPromoState.confirm)
    data = await state.get_data()
    await edit_or_answer(
        callback,
        _summary(i18n, lang, data),
        reply_markup=promo_confirm_keyboard(i18n, lang),
    )


async def promo_confirm(
    callback: CallbackQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    lang = callback_language(callback, i18n)
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    data = await state.get_data()

    created = False
    try:
        async with session_scope(session_factory) as session:
            admin_user = await ensure_minimal_user(session, tg_id=user.id, i18n=i18n)
            await create_promo(
                session,
                code=data["code"],
                kind=PromoKind(data["kind"]),
                value=Decimal(data["value"]),
                created_by=admin_user.id,
                max_uses=data.get("max_uses"),
                expires_at=(
                    datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
                ),
                allowed_tariffs=data.get("allowed_tariffs") or None,
            )
        created = True
    except IntegrityError:
        created = False
    except SQLAlchemyError:
        logger.exception("failed to create promo")
        await state.set_state(None)
        await edit_or_answer(
            callback,
            i18n.gettext("error.generic", lang),
            reply_markup=back_keyboard(i18n, lang),
        )
        return

    if not created:
        # Lost the unique-code race; send the admin back to re-enter a code.
        await state.set_state(AdminPromoState.code)
        await edit_or_answer(
            callback,
            i18n.gettext("admin.promo.duplicate", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return

    await state.set_state(None)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.promo.created", lang),
        reply_markup=back_keyboard(i18n, lang),
    )


# --- helpers --------------------------------------------------------------------------------


async def _tariffs_view(i18n: JsonI18n, settings: Settings, state: FSMContext, lang: str):
    await state.set_state(AdminPromoState.allowed_tariffs)
    data = await state.get_data()
    selected = data.get("allowed_tariffs") or []
    text = i18n.gettext("admin.promo.new.tariffs", lang)
    keyboard = promo_tariffs_keyboard(i18n, lang, settings, selected=selected)
    return text, keyboard


def _summary(i18n: JsonI18n, lang: str, data: dict) -> str:
    max_uses = data.get("max_uses")
    expires = data.get("expires_at")
    tariffs = data.get("allowed_tariffs")
    return i18n.gettext(
        "admin.promo.new.confirm",
        lang,
        code=html.escape(data["code"]),
        kind=i18n.gettext(f"admin.promo.kind.{data['kind']}", lang),
        value=data["value"],
        max_uses="∞" if max_uses is None else max_uses,
        expires=expires[:10] if expires else "—",
        tariffs=", ".join(tariffs) if tariffs else "—",
    )


def _validate_value(kind: PromoKind, text: str) -> Decimal | None:
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    if kind is PromoKind.percent and value > 100:
        return None
    if kind is PromoKind.extra_days and value != value.to_integral_value():
        return None
    return value


def _parse_expires(text: str) -> datetime:
    parsed = datetime.strptime(text, "%Y-%m-%d").replace(  # noqa: DTZ007 - tz set on next line
        tzinfo=UTC,
        hour=23,
        minute=59,
        second=59,
    )
    if parsed <= datetime.now(UTC):
        raise ValueError
    return parsed


def _suffix(data: str | None, prefix: str) -> str | None:
    if not data or not data.startswith(prefix):
        return None
    return data.removeprefix(prefix)


def _parse_int_suffix(data: str | None, prefix: str) -> int | None:
    raw = _suffix(data, prefix)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
