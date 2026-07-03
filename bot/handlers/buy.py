from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LabeledPrice
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.service_events import fmt_amount, fmt_dt, money, render_event, user_mention
from bot.handlers.start import callback_context
from bot.keyboards import (
    CB_BACK_DURATION_PREFIX,
    CB_BACK_TARIFFS,
    CB_BUY,
    CB_CRYPTOBOT_PREFIX,
    CB_PLAN_PREFIX,
    CB_REFERRAL_PAY_PREFIX,
    CB_STARS_PREFIX,
    CB_TARIFF_PREFIX,
    CB_TRIBUTE_PREFIX,
    CB_XROCKET_PREFIX,
    payment_keyboard,
    plan_keyboard,
    tariff_detail_keyboard,
    tariff_keyboard,
)
from bot.models import User, UserStatus
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotClient
from bot.payments.xrocket import XRocketAPIError, XRocketClient
from bot.repos.promos import PromoUnavailable, get_pending_promo_for_menu
from bot.repos.referrals import (
    InsufficientReferralBalance,
    get_referral_stats,
    spend_referral_balance,
)
from bot.repos.subscriptions import extend_manual_subscription, get_subscription_for_user
from bot.repos.tariffs import get_plan_tariffs, get_tariff, resolve_plan_code
from bot.repos.users import ensure_minimal_user
from bot.services.access import send_access_links
from bot.services.payments import (
    cancel_unusable_invoice,
    create_cryptobot_invoice_for_user,
    create_stars_invoice_for_user,
    create_xrocket_invoice_for_user,
)
from bot.services.pricing import PricedTariff, TariffQuote, quote_tariff, tariff_duration_days
from bot.services.profile import profile_plan_label
from bot.services.subscriptions import effective_status

logger = logging.getLogger(__name__)

CALLBACK_ANSWER_TEXT_LIMIT = 200


async def buy_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    now = datetime.now(UTC)
    lang_code = i18n.resolve_language(context.language_code)
    current = i18n.gettext("start.status.free", lang_code)
    try:
        if context.tg_id is not None:
            async with session_scope(session_factory) as session:
                user = await ensure_minimal_user(
                    session,
                    tg_id=context.tg_id,
                    i18n=i18n,
                    language_code=context.language_code,
                )
                lang_code = user.lang_code
                subscription = await get_subscription_for_user(session, user.id)
                current = profile_plan_label(settings, i18n, lang_code, subscription, now)
    except SQLAlchemyError:
        logger.exception("failed to resolve buy callback user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    # Menu 1 — tariff (product) selection. Always shown, even with a single plan.
    text = f"{i18n.gettext('buy.title', lang_code)}\n\n{_current_line(i18n, lang_code, current)}"
    await _edit_text_or_answer(
        callback,
        text,
        reply_markup=plan_keyboard(i18n, settings, lang_code),
    )


async def plan_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Menu 2 — duration selection for the chosen plan."""
    context = callback_context(callback, settings)
    plan_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_PLAN_PREFIX)
    if plan_code is None:
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _show_duration_menu(callback, settings, i18n, session_factory, plan_code)


async def back_to_tariffs_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Back from Menu 2 to Menu 1."""
    await buy_callback(callback, settings, i18n, session_factory)


async def back_to_duration_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Back from Menu 3 (quote/payment) to Menu 2 (durations)."""
    context = callback_context(callback, settings)
    plan_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_BACK_DURATION_PREFIX)
    if plan_code is None:
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _show_duration_menu(callback, settings, i18n, session_factory, plan_code)


async def _show_duration_menu(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    plan_code: str,
) -> None:
    context = callback_context(callback, settings)
    try:
        text, keyboard, _lang = await build_duration_menu(
            settings,
            i18n,
            session_factory,
            tg_id=context.tg_id,
            language_code=context.language_code,
            plan_code=plan_code,
        )
    except SQLAlchemyError:
        logger.exception("failed to resolve duration menu user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    await _edit_text_or_answer(callback, text, reply_markup=keyboard)


def _current_line(i18n: JsonI18n, lang_code: str, current: str) -> str:
    return i18n.gettext("buy.current", lang_code, current=current)


def _plan_description(i18n: JsonI18n, lang_code: str, plan_code: str) -> str:
    """Localized plan description (bullet list) from ``plan.<code>.description``; empty when the
    key is absent (JsonI18n returns the key itself for a miss)."""
    key = f"plan.{plan_code}.description"
    text = i18n.gettext(key, lang_code)
    return "" if text == key else text


def _duration_menu_text(i18n: JsonI18n, lang_code: str, plan_code: str, current: str) -> str:
    parts = [i18n.gettext("buy.duration.title", lang_code)]
    description = _plan_description(i18n, lang_code, plan_code)
    if description:
        parts.append(description)
    parts.append(_current_line(i18n, lang_code, current))
    return "\n\n".join(parts)


def _priced_no_promo(
    settings: Settings, plan_code: str, status: UserStatus
) -> list[PricedTariff]:
    """Per-duration prices with the loyalty ``status`` applied but no promo (no user context)."""
    priced: list[PricedTariff] = []
    for tariff in get_plan_tariffs(settings, plan_code):
        net = quote_tariff(settings, tariff.code, status).net_usd
        priced.append(
            PricedTariff(
                tariff=tariff, old_usd=net, new_usd=net, promo_discount_usd=Decimal("0.00")
            )
        )
    return priced


async def _priced_tariffs(
    session: AsyncSession,
    settings: Settings,
    plan_code: str,
    user: User,
    status: UserStatus,
    now: datetime,
) -> list[PricedTariff]:
    """Per-duration prices with loyalty AND the user's pending promo. The promo is resolved ONCE,
    non-destructively (see ``get_pending_promo_for_menu``), and applied only to the tariffs it is
    valid for — so browsing the menu never drops a tariff-restricted pending promo."""
    promo, allowed = await get_pending_promo_for_menu(session, user=user, now=now)
    priced: list[PricedTariff] = []
    for tariff in get_plan_tariffs(settings, plan_code):
        applies = promo is not None and (not allowed or tariff.code in allowed)
        quote = quote_tariff(settings, tariff.code, status, promo=promo if applies else None)
        priced.append(
            PricedTariff(
                tariff=tariff,
                old_usd=quote.net_usd + quote.promo_discount_usd,
                new_usd=quote.net_usd,
                promo_discount_usd=quote.promo_discount_usd,
            )
        )
    return priced


async def build_duration_menu(
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tg_id: int | None,
    language_code: str | None,
    plan_code: str,
    banner: str = "",
) -> tuple[str, InlineKeyboardMarkup, str]:
    """Build Menu 2: text (title + localized plan description + current plan) and a keyboard
    whose prices reflect the user's loyalty tier and pending promo. Returns
    ``(text, keyboard, lang_code)``; an optional ``banner`` is prefixed (e.g. after a promo)."""
    now = datetime.now(UTC)
    lang_code = i18n.resolve_language(language_code)
    if tg_id is None:
        priced = _priced_no_promo(settings, plan_code, UserStatus.basic)
        current = i18n.gettext("start.status.free", lang_code)
    else:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session, tg_id=tg_id, i18n=i18n, language_code=language_code
            )
            lang_code = user.lang_code
            subscription = await get_subscription_for_user(session, user.id)
            status = effective_status(
                user.status,
                user.total_active_days,
                subscription.expires_at if subscription is not None else None,
                now,
                settings.loyalty,
            )
            priced = await _priced_tariffs(session, settings, plan_code, user, status, now)
            current = profile_plan_label(settings, i18n, lang_code, subscription, now)

    text = _duration_menu_text(i18n, lang_code, plan_code, current)
    if banner:
        text = f"{banner}\n\n{text}"
    return text, tariff_keyboard(i18n, lang_code, plan_code, priced), lang_code


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
            now = datetime.now(UTC)
            # Resolve the pending promo non-destructively (see get_pending_promo_for_menu): merely
            # viewing a tariff's quote must not wipe a promo restricted to a different tariff. The
            # promo applies here only if this tariff is in its allowed set (empty = all tariffs).
            promo_snapshot, allowed = await get_pending_promo_for_menu(session, user=user, now=now)
            applies = promo_snapshot is not None and (not allowed or tariff_code in allowed)
            subscription = await get_subscription_for_user(session, user.id)
            status = effective_status(
                user.status,
                user.total_active_days,
                subscription.expires_at if subscription is not None else None,
                now,
                settings.loyalty,
            )
            quote = quote_tariff(
                settings, tariff_code, status, promo=promo_snapshot if applies else None
            )
            lang_code = user.lang_code
            referral_stats = await get_referral_stats(session, referrer_id=user.id)
            show_referral_pay = referral_stats.available_usd >= quote.net_usd
    except SQLAlchemyError:
        logger.exception("failed to resolve tariff callback user")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except LookupError:
        logger.info("invalid tariff callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except PromoUnavailable as exc:
        await callback.answer(_promo_error_text(i18n, lang_code, exc.reason), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        _format_quote_text(i18n, settings, lang_code, quote),
        reply_markup=tariff_detail_keyboard(
            i18n,
            tariff_code,
            lang_code,
            settings,
            show_referral_pay=show_referral_pay,
        ),
    )


async def cryptobot_invoice_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    cryptobot_client: CryptoBotClient | None = None,
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

    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            lang_code = user.lang_code
    except SQLAlchemyError:
        logger.exception("failed to resolve CryptoBot invoice user")
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return

    try:
        created = await create_cryptobot_invoice_for_user(
            session_factory,
            settings,
            cryptobot_client,
            user,
            tariff_code,
            _support_url(settings),
            datetime.now(UTC),
        )
    except SQLAlchemyError:
        logger.exception("failed to persist CryptoBot invoice")
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
    except PromoUnavailable as exc:
        await callback.answer(_promo_error_text(i18n, lang_code, exc.reason), show_alert=True)
        return
    except Exception:
        logger.exception("unexpected CryptoBot invoice creation failure")
        await callback.answer(
            i18n.gettext("buy.invoice.failed", lang_code),
            show_alert=True,
        )
        return

    await _edit_text_or_answer(
        callback,
        _invoice_created_text(i18n, lang_code, created.pay_url),
        reply_markup=payment_keyboard(i18n, settings, lang_code, created.pay_url),
    )


async def stars_invoice_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_STARS_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return
    if not settings.stars.enabled:
        await callback.answer(
            i18n.gettext("buy.invoice.failed", context.language_code),
            show_alert=True,
        )
        return

    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            lang_code = user.lang_code
    except SQLAlchemyError:
        logger.exception("failed to resolve Stars invoice user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    try:
        created = await create_stars_invoice_for_user(
            session_factory,
            settings,
            user,
            tariff_code,
            datetime.now(UTC),
            i18n,
        )
        invoice_kwargs = {
            "title": created.title,
            "description": created.description,
            "payload": created.payload,
            "currency": created.stars.currency,
            "prices": [
                LabeledPrice(
                    label=created.title,
                    amount=created.stars.amount,
                )
            ],
            "provider_token": "",
        }
        if created.stars.subscription_period is not None:
            invoice_kwargs["subscription_period"] = created.stars.subscription_period
        try:
            await callback.message.answer_invoice(**invoice_kwargs)
        except Exception:
            await cancel_unusable_invoice(session_factory, created.invoice.id)
            raise
    except SQLAlchemyError:
        logger.exception("failed to persist Stars invoice")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except LookupError:
        logger.info("invalid Stars invoice callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except PromoUnavailable as exc:
        await callback.answer(_promo_error_text(i18n, lang_code, exc.reason), show_alert=True)
        return
    except Exception:
        logger.exception("unexpected Stars invoice creation failure")
        await callback.answer(i18n.gettext("buy.invoice.failed", lang_code), show_alert=True)
        return

    await callback.answer()


async def xrocket_invoice_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    xrocket_client: XRocketClient | None = None,
) -> None:
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_XROCKET_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return
    if not settings.xrocket.enabled or xrocket_client is None:
        await callback.answer(
            i18n.gettext("buy.invoice.failed", context.language_code),
            show_alert=True,
        )
        return

    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            lang_code = user.lang_code
    except SQLAlchemyError:
        logger.exception("failed to resolve xRocket invoice user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    try:
        created = await create_xrocket_invoice_for_user(
            session_factory,
            settings,
            xrocket_client,
            user,
            tariff_code,
            datetime.now(UTC),
        )
    except SQLAlchemyError:
        logger.exception("failed to persist xRocket invoice")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except XRocketAPIError:
        logger.exception("xRocket invoice creation failed")
        await callback.answer(
            i18n.gettext("buy.invoice.failed", lang_code),
            show_alert=True,
        )
        return
    except LookupError:
        logger.info("invalid xRocket invoice callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except PromoUnavailable as exc:
        await callback.answer(_promo_error_text(i18n, lang_code, exc.reason), show_alert=True)
        return
    except Exception:
        logger.exception("unexpected xRocket invoice creation failure")
        await callback.answer(
            i18n.gettext("buy.invoice.failed", lang_code),
            show_alert=True,
        )
        return

    await _edit_text_or_answer(
        callback,
        _invoice_created_text(i18n, lang_code, created.pay_url),
        reply_markup=payment_keyboard(
            i18n,
            settings,
            lang_code,
            created.pay_url,
            payment_key="buy.pay.xrocket",
        ),
    )


async def tribute_invoice_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_TRIBUTE_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return

    # Tribute has no create-invoice API: the checkout link is a static, per-tariff URL
    # configured in settings; settlement arrives later via the Tribute webhook.
    pay_url = settings.tribute.link_for_tariff(tariff_code) if settings.tribute.enabled else None
    if pay_url is None:
        await callback.answer(
            i18n.gettext("buy.invoice.failed", context.language_code),
            show_alert=True,
        )
        return

    try:
        lang_code = await _resolve_user_language(
            context.tg_id,
            context.language_code,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to resolve Tribute invoice user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    await _edit_text_or_answer(
        callback,
        _invoice_created_text(i18n, lang_code, pay_url),
        reply_markup=payment_keyboard(
            i18n,
            settings,
            lang_code,
            pay_url,
            payment_key="buy.pay.tribute",
        ),
    )


async def referral_pay_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    service_channel: ServiceChannel,
) -> None:
    """Grant a subscription paid entirely from the user's referral balance.

    Unlike the provider callbacks, there is no external payment round-trip: the balance is
    debited and the subscription granted atomically here, then access links + a service-channel
    event are sent. The balance is re-checked under a row lock (``spend_referral_balance``), so a
    stale/oversold button falls back to an ``InsufficientReferralBalance`` alert.
    """
    context = callback_context(callback, settings)
    tariff_code = _parse_prefixed_callback(getattr(callback, "data", None), CB_REFERRAL_PAY_PREFIX)
    if tariff_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return

    now = datetime.now(UTC)
    lang_code = i18n.resolve_language(context.language_code)
    price = Decimal("0.00")
    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            lang_code = user.lang_code
            username = user.username
            tariff = get_tariff(settings, tariff_code)
            current_subscription = await get_subscription_for_user(session, user.id)
            status = effective_status(
                user.status,
                user.total_active_days,
                current_subscription.expires_at if current_subscription is not None else None,
                now,
                settings.loyalty,
            )
            price = quote_tariff(settings, tariff_code, status).net_usd
            plan_code = resolve_plan_code(settings, tariff_code)
            remaining = await spend_referral_balance(session, user=user, amount=price)
            subscription = await extend_manual_subscription(
                session,
                user=user,
                days=tariff_duration_days(tariff.duration_months),
                now=now,
                loyalty=settings.loyalty,
                plan_code=plan_code,
            )
            expires_at = subscription.expires_at
    except InsufficientReferralBalance as exc:
        await callback.answer(
            i18n.gettext(
                "buy.referral.insufficient",
                lang_code,
                available=f"{exc.available:.2f}",
                price=f"{price:.2f}",
            ),
            show_alert=True,
        )
        return
    except LookupError:
        logger.info("invalid referral pay callback data: %r", getattr(callback, "data", None))
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return
    except SQLAlchemyError:
        logger.exception("failed to process referral-balance purchase")
        await callback.answer(i18n.gettext("error.generic", lang_code), show_alert=True)
        return

    await _notify_referral_purchase(
        service_channel,
        tg_id=context.tg_id,
        username=username,
        tariff_code=tariff_code,
        net=price,
        plan_code=plan_code,
        remaining=remaining,
        expires_at=expires_at,
    )

    try:
        await send_access_links(
            bot,
            settings,
            i18n,
            tg_id=context.tg_id,
            lang_code=lang_code,
            now=now,
            plan_code=plan_code,
        )
    except Exception:
        logger.exception(
            "failed to send referral purchase access links",
            extra={"tg_id": context.tg_id},
        )

    await _edit_text_or_answer(
        callback,
        i18n.gettext("buy.referral.success", lang_code, expires_at=_format_expiry(expires_at)),
    )


async def _notify_referral_purchase(
    service_channel: ServiceChannel,
    *,
    tg_id: int,
    username: str | None,
    tariff_code: str,
    net: Decimal,
    plan_code: str | None,
    remaining: Decimal,
    expires_at: datetime,
) -> None:
    try:
        await service_channel.send_html(
            render_event(
                glyph="check",
                title="Referral balance purchase",
                fields=[
                    ("User", user_mention(tg_id, username)),
                    ("Paid", money(net, "USD")),
                ],
                extra={
                    "tariff": tariff_code,
                    "plan": plan_code or "—",
                    "net": f"{fmt_amount(net)} USD",
                    "paid_from": "referral_balance",
                    "remaining_balance": f"{fmt_amount(remaining)} USD",
                    "expires_at": fmt_dt(expires_at),
                },
            )
        )
    except Exception:
        logger.exception(
            "failed to send referral purchase event",
            extra={"tg_id": tg_id},
        )


def _format_expiry(expires_at: datetime) -> str:
    return expires_at.strftime("%Y-%m-%d %H:%M")


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


def _format_quote_text(
    i18n: JsonI18n,
    settings: Settings,
    lang_code: str,
    quote: TariffQuote,
) -> str:
    text = i18n.gettext(
        "buy.quote.text",
        lang_code,
        months=quote.tariff.duration_months,
        gross_usd=f"{quote.gross_usd:.2f}",
        net_usd=f"{quote.net_usd:.2f}",
        fee_usd=f"{quote.fee_usd:.2f}",
        accepted_assets=", ".join(settings.cryptobot.accepted_assets),
    )
    if quote.promo_code is not None and quote.promo_discount_usd > 0:
        text = "\n".join(
            [
                text,
                i18n.gettext(
                    "buy.quote.promo_discount",
                    lang_code,
                    code=quote.promo_code,
                    discount_usd=f"{quote.promo_discount_usd:.2f}",
                ),
            ]
        )
    if quote.promo_code is not None and quote.promo_extra_days > 0:
        text = "\n".join(
            [
                text,
                i18n.gettext(
                    "buy.quote.promo_extra_days",
                    lang_code,
                    code=quote.promo_code,
                    days=quote.promo_extra_days,
                ),
            ]
        )
    return text


def _parse_prefixed_callback(data: str | None, prefix: str) -> str | None:
    if data is None or not data.startswith(prefix):
        return None
    value = data.removeprefix(prefix).strip()
    return value or None


def _invoice_created_text(i18n: JsonI18n, lang_code: str, pay_url: str) -> str:
    return f"{i18n.gettext('buy.invoice.created', lang_code)}\n{pay_url}"


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


def _promo_error_text(i18n: JsonI18n, lang_code: str, reason: str) -> str:
    key = f"promo.error.{reason}"
    text = i18n.gettext(key, lang_code)
    if text == key:
        return i18n.gettext("promo.error.generic", lang_code)
    return text


def create_buy_router() -> Router:
    router = Router(name="buy")
    router.callback_query(F.data == CB_BUY)(buy_callback)
    router.callback_query(F.data == CB_BACK_TARIFFS)(back_to_tariffs_callback)
    router.callback_query(F.data.startswith(CB_BACK_DURATION_PREFIX))(back_to_duration_callback)
    router.callback_query(F.data.startswith(CB_PLAN_PREFIX))(plan_callback)
    router.callback_query(F.data.startswith(CB_TARIFF_PREFIX))(tariff_callback)
    router.callback_query(F.data.startswith(CB_CRYPTOBOT_PREFIX))(cryptobot_invoice_callback)
    router.callback_query(F.data.startswith(CB_STARS_PREFIX))(stars_invoice_callback)
    router.callback_query(F.data.startswith(CB_XROCKET_PREFIX))(xrocket_invoice_callback)
    router.callback_query(F.data.startswith(CB_TRIBUTE_PREFIX))(tribute_invoice_callback)
    router.callback_query(F.data.startswith(CB_REFERRAL_PAY_PREFIX))(referral_pay_callback)
    return router
