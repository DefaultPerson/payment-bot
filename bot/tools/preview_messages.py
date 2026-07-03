"""Send a design preview of every bot message to one chat (default: an admin DM).

Renders each outbound message — user-facing screens with their inline keyboards,
lifecycle notifications, and service-channel events — exactly the way production
builds them (real i18n templates, real keyboard builders with their custom-emoji
button icons, and ``render_event`` glyphs), then delivers them all to a single chat
so the whole design can be reviewed in one place.

It is deliberately **read-only**: no database access, no *stored* invoice creation, and
every message is sent to the one target chat, never to real users. Custom-emoji ids are
validated against Telegram first (same as ``bot.__main__`` does at startup) so a stale id
degrades to plain unicode instead of failing a send.

Out of scope (documented gaps, not reproduced): the multi-step *immediate* broadcast
composer/confirm/progress screens are rendered by the third-party ``aiogram_broadcast``
library at runtime and have no static template to preview. The Telegram Stars checkout is
shown as a real native invoice card (``send_invoice``); tapping "Pay" cannot complete
because no pre-checkout handler runs, so no charge occurs.

Usage:
    uv run python -m bot.tools.preview_messages                 # -> first admin id
    uv run python -m bot.tools.preview_messages --chat-id 123
    uv run python -m bot.tools.preview_messages --lang en        # ru | ua | en
    uv run python -m bot.tools.preview_messages --only events    # user|notify|events|admin
    uv run python -m bot.tools.preview_messages --no-labels       # skip separator labels
    uv run python -m bot.tools.preview_messages --dry-run         # print, do not send
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import TextIO

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, LabeledPrice

from bot.config import Settings, load_settings
from bot.core.i18n import JsonI18n
from bot.core.logger import _compact_html
from bot.core.service_events import (
    apply_valid_event_emoji,
    configured_event_emoji_ids,
    disable_event_emoji,
    fmt_dt,
    money,
    render_event,
    user_mention,
)
from bot.core.text_emoji import text_emoji
from bot.handlers.admin.broadcast import _segment_keyboard
from bot.handlers.admin.stats import _format_stats
from bot.handlers.buy import _duration_menu_text, _format_quote_text, _priced_no_promo
from bot.handlers.promo import _fmt_promo_value, _promo_cancel_keyboard
from bot.handlers.start import build_start_text
from bot.keyboards.admin import (
    back_keyboard,
    cancel_keyboard,
    promo_confirm_keyboard,
    promo_kind_keyboard,
    promo_list_keyboard,
    promo_tariffs_keyboard,
    root_keyboard,
    skip_cancel_keyboard,
    stats_keyboard,
)
from bot.keyboards.icons import apply_valid_icon_ids, configured_icon_ids, disable_icons
from bot.keyboards.menu import (
    language_keyboard,
    main_menu_keyboard,
    payment_keyboard,
    plan_keyboard,
    profile_keyboard,
    support_keyboard,
    tariff_detail_keyboard,
    tariff_keyboard,
    trial_confirm_keyboard,
)
from bot.models import InvoiceProvider, Promo, PromoKind, UserStatus
from bot.repos.stats import ProviderRevenue, RevenueTotals, TopReferrer
from bot.services.broadcast_ui import _DONE_TEXT
from bot.services.notifications import format_subscription_notification
from bot.services.pricing import quote_tariff
from bot.services.profile import ProfileView, format_profile_text
from bot.services.stats import StatsPeriod, StatsSnapshot

# --- Fixed sample data (deterministic, so previews read the same every run) --------------
_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_EXPIRES = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
_TG = 123456789
_USERNAME = "hydra_user"
_PAY_URL = "https://t.me/CryptoBot"
_QUOTE_CODE = "1m"
_EXPIRES_SHORT = _EXPIRES.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True, slots=True)
class Preview:
    """One renderable message: its section, a human label, the text and optional keyboard."""

    section: str
    label: str
    text: str
    markup: InlineKeyboardMarkup | None = None
    is_event: bool = False  # service-channel event -> compact HTML + no link preview
    invoice: dict | None = None  # native Telegram invoice card -> send_invoice, not send_message


# ---------------------------------------------------------------------------------------
# USER — main menu, profile, buy funnel, payment, trial, promo, referral, language, misc
# ---------------------------------------------------------------------------------------
def _user_previews(i18n: JsonI18n, settings: Settings, lang: str) -> list[Preview]:
    g = lambda key, **kw: i18n.gettext(key, lang, **kw)  # noqa: E731
    support_url = settings.bot.support_url
    start_text = build_start_text(i18n, settings, lang, status_label=g("start.status.free"))
    trial_cta = g("start.trial.cta", days=settings.trial.duration_days, new=text_emoji("new"))

    base_quote = quote_tariff(settings, _QUOTE_CODE, UserStatus.basic)
    promo_discount_quote = replace(
        base_quote, promo_code="WELCOME10", promo_discount_usd=Decimal("7.90")
    )
    promo_days_quote = replace(base_quote, promo_code="PLUS7", promo_extra_days=7)

    invite_link = f"https://t.me/{settings.bot.username}?start=ref_{_TG}"
    min_withdraw_usd = settings.referral.min_withdraw_usd
    profile_active = ProfileView(
        tg_id=_TG,
        status=UserStatus.silver,
        total_active_days=124,
        subscription_active=True,
        expires_at=_EXPIRES,
        days_to_next_tier=56,
        referral_count=8,
        referral_earned_usd=Decimal("42.50"),
        referral_unpaid_earned_usd=Decimal("22.50"),
        invite_link=invite_link,
        min_withdraw_usd=min_withdraw_usd,
        plan_label="Main",
        referral_percent=Decimal("10"),
    )
    profile_inactive = ProfileView(
        tg_id=_TG,
        status=UserStatus.basic,
        total_active_days=0,
        subscription_active=False,
        expires_at=None,
        days_to_next_tier=90,
        referral_count=0,
        referral_earned_usd=Decimal("0.00"),
        referral_unpaid_earned_usd=Decimal("0.00"),
        invite_link=invite_link,
        min_withdraw_usd=min_withdraw_usd,
        plan_label=g("start.status.free"),
        referral_percent=Decimal("10"),
    )

    access_links = "\n".join(
        f"{escape(chat.title)}: https://t.me/+SAMPLEinvite{index}"
        for index, chat in enumerate(settings.access.protected_chats, start=1)
    ) or "Hydra Monitors: https://t.me/+SAMPLEinvite"

    referral_text = g(
        "referral.text",
        invite_link=f"https://t.me/{settings.bot.username}?start=ref_{_TG}",
        referral_count=8,
        referral_earned_usd="42.50",
        referral_unpaid_earned_usd="22.50",
        min_withdraw_usd=f"{settings.referral.min_withdraw_usd:.2f}",
    )

    main_priced = _priced_no_promo(settings, "main", UserStatus.basic)
    pro_priced = _priced_no_promo(settings, "pro", UserStatus.basic)

    return [
        Preview("user", "01 · /start — main menu (new user, no admin)",
                start_text, main_menu_keyboard(i18n, lang, settings, is_admin=False)),
        Preview("user", "02 · /start — main menu + trial CTA",
                f"{start_text}\n\n{trial_cta}",
                main_menu_keyboard(i18n, lang, settings, is_admin=False, show_trial=True)),
        Preview("user", "03 · /start — main menu (admin, admin button)",
                start_text,
                main_menu_keyboard(i18n, lang, settings, is_admin=True, show_trial=True)),
        Preview("user", "04 · /start — referral link applied",
                f"{start_text}\n\n{g('referral.start.applied')}",
                main_menu_keyboard(i18n, lang, settings, is_admin=False)),
        Preview("user", "05 · /start — referral rebind rejected",
                f"{start_text}\n\n{g('referral.start.rebind_rejected')}",
                main_menu_keyboard(i18n, lang, settings, is_admin=False)),
        Preview("user", "06 · Profile — active subscription",
                format_profile_text(profile_active, i18n, lang),
                profile_keyboard(i18n, settings, lang)),
        Preview("user", "07 · Profile — inactive",
                format_profile_text(profile_inactive, i18n, lang),
                profile_keyboard(i18n, settings, lang)),
        Preview("user", "08 · Buy — plan (product) selection",
                f'{g("buy.title")}\n\n{g("buy.current", current=g("start.status.free"))}',
                plan_keyboard(i18n, settings, lang)),
        Preview("user", "09 · Buy — duration menu (main plan)",
                _duration_menu_text(settings, i18n, lang, "main", g("start.status.free")),
                tariff_keyboard(i18n, lang, "main", main_priced)),
        Preview("user", "10 · Buy — duration menu (pro plan)",
                _duration_menu_text(settings, i18n, lang, "pro", g("start.status.free")),
                tariff_keyboard(i18n, lang, "pro", pro_priced)),
        Preview("user", "11 · Buy — quote + payment methods",
                _format_quote_text(i18n, settings, lang, base_quote),
                tariff_detail_keyboard(i18n, _QUOTE_CODE, lang, settings, base_quote)),
        Preview("user", "12 · Buy — quote + referral-balance button",
                _format_quote_text(i18n, settings, lang, base_quote),
                tariff_detail_keyboard(i18n, _QUOTE_CODE, lang, settings, base_quote,
                                       show_referral_pay=True)),
        Preview("user", "13 · Buy — quote with promo discount",
                _format_quote_text(i18n, settings, lang, promo_discount_quote),
                tariff_detail_keyboard(i18n, _QUOTE_CODE, lang, settings, promo_discount_quote)),
        Preview("user", "14 · Buy — quote with promo extra days",
                _format_quote_text(i18n, settings, lang, promo_days_quote),
                tariff_detail_keyboard(i18n, _QUOTE_CODE, lang, settings, promo_days_quote)),
        Preview("user", "15 · Invoice created — CryptoBot", g("buy.invoice.created", warn="❗"),
                payment_keyboard(i18n, settings, lang, _PAY_URL, _QUOTE_CODE,
                                 payment_key="buy.pay.cryptobot")),
        Preview("user", "16 · Invoice card — Stars (NATIVE; title/desc hardcoded EN)",
                "", invoice={
                    "title": "Hydra Monitors",
                    "description": "Hydra Monitors access: 1 month",
                    "payload": "preview-stars",
                    "currency": "XTR",
                    "prices": [LabeledPrice(label="Hydra Monitors", amount=1)],
                    "provider_token": "",
                }),
        Preview("user", "17 · Invoice created — xRocket", g("buy.invoice.created", warn="❗"),
                payment_keyboard(i18n, settings, lang, _PAY_URL, _QUOTE_CODE,
                                 payment_key="buy.pay.xrocket")),
        Preview("user", "18 · Invoice created — Tribute (card)",
                g("buy.invoice.created", warn="❗"),
                payment_keyboard(i18n, settings, lang, _PAY_URL, _QUOTE_CODE,
                                 payment_key="buy.pay.tribute")),
        Preview("user", "19 · Invoice failed", g("buy.invoice.failed")),
        Preview("user", "20 · Access links delivered",
                g("access.links.text", sparkles="✨", links=access_links)
                + "\n\n" + g("access.links.footer", docs_url=settings.bot.docs_url)),
        Preview("user", "22 · Access — no chats configured",
                g("access.empty.text", support_url=support_url)),
        Preview("user", "23 · Trial — confirm dialog",
                g("trial.confirm.text", days=settings.trial.duration_days, plan="HMonitors PRO"),
                trial_confirm_keyboard(i18n, lang)),
        Preview("user", "24 · Trial — started",
                g("trial.success.text", days=settings.trial.duration_days,
                  expires_at=_EXPIRES_SHORT, plan="HMonitors PRO")),
        Preview("user", "25 · Trial — states (alerts)",
                "\n\n".join((
                    "• already_used: " + g("trial.already_used"),
                    "• has_active: " + g("trial.has_active"),
                    "• unavailable: " + g("trial.unavailable"),
                ))),
        Preview("user", "26 · Referral — success (paid from balance)",
                g("buy.referral.success", expires_at=_EXPIRES_SHORT)),
        Preview("user", "27 · Referral — insufficient balance (alert)",
                g("buy.referral.insufficient", available="15.00", price="79.00")),
        Preview("user", "28 · Referral — program screen", referral_text),
        Preview("user", "29 · Promo — prompt / entry",
                g("promo.enter"), _promo_cancel_keyboard(i18n, lang)),
        Preview("user", "30 · Promo — applied", g("promo.applied", code="WELCOME10")),
        Preview("user", "31 · Promo — prompts & errors (alerts)",
                "\n".join((
                    "• prompt: " + g("promo.prompt"),
                    "• usage: " + g("promo.usage"),
                    "• " + g("promo.error.not_found"),
                    "• " + g("promo.error.inactive"),
                    "• " + g("promo.error.expired"),
                    "• " + g("promo.error.exhausted"),
                    "• " + g("promo.error.invalid_value"),
                    "• " + g("promo.error.tariff_not_allowed"),
                    "• " + g("promo.error.already_used"),
                    "• " + g("promo.error.generic"),
                ))),
        Preview("user", "32 · Language — menu",
                g("commands.language"), language_keyboard(i18n, lang)),
        Preview("user", "33 · Language — changed",
                g("language.changed", language_name=g(f"language.name.{lang}")),
                main_menu_keyboard(i18n, lang, settings, is_admin=False)),
        Preview("user", "34 · Support", g("support.text", support_url=support_url),
                support_keyboard(i18n, settings, lang)),
        Preview("user", "35 · Misc — coming soon / generic error",
                "• coming_soon: " + g("coming_soon.text") + "\n• error: " + g("error.generic")),
    ]


# ---------------------------------------------------------------------------------------
# NOTIFY — subscription-lifecycle DM reminders
# ---------------------------------------------------------------------------------------
def _notify_previews(i18n: JsonI18n, settings: Settings, lang: str) -> list[Preview]:
    labels = {
        "3d": "36 · Notification — 3 days before expiry",
        "0d": "37 · Notification — day of expiry",
        "post": "38 · Notification — access revoked (post-expiry)",
    }
    return [
        Preview("notify", labels[kind],
                format_subscription_notification(
                    i18n, kind, lang, expires_at=_EXPIRES, grace_days=7))
        for kind in ("3d", "0d", "post")
    ]


# ---------------------------------------------------------------------------------------
# EVENTS — service-channel posts (custom-emoji glyph + collapsed "Additional Info")
# ---------------------------------------------------------------------------------------
def _event_previews() -> list[Preview]:
    who = user_mention(_TG, _USERNAME)
    events = [
        ("39 · Event — CryptoBot payment", render_event(
            glyph="coin", title="Cryptobot payment paid",
            fields=[("User", who), ("Payment", money(Decimal("79.00"), "USDT"))],
            extra={"provider_invoice_id": "IV123456", "tariff": "1m",
                   "gross": "79.00 USDT", "fee": "2.37 USDT", "net": "76.63 USDT",
                   "expires_at": fmt_dt(_EXPIRES)})),
        ("40 · Event — xRocket payment", render_event(
            glyph="coin", title="Xrocket payment paid",
            fields=[("User", who), ("Payment", money(Decimal("79.00"), "USDT"))],
            extra={"provider_invoice_id": "XR987654", "tariff": "1m",
                   "gross": "79.00 USDT", "fee": "0.00 USDT", "net": "79.00 USDT",
                   "expires_at": fmt_dt(_EXPIRES)})),
        ("41 · Event — Tribute (card) payment", render_event(
            glyph="coin", title="Tribute payment paid",
            fields=[("User", who), ("Payment", money(Decimal("72.00"), "EUR"))],
            extra={"tariff": "1m", "product_id": "prod_42", "purchase_id": "pur_1001",
                   "amount": "72.00 EUR", "net_usd": "76.63 USD",
                   "expires_at": fmt_dt(_EXPIRES)})),
        ("42 · Event — Telegram Stars payment", render_event(
            glyph="star", title="Telegram stars payment paid",
            fields=[("User", who), ("Payment", money(Decimal("1"), "XTR"))],
            extra={"tariff": "test", "gross": "1.00 XTR", "fee": "0.00 XTR",
                   "net": "1.00 XTR", "expires_at": fmt_dt(_EXPIRES)})),
        ("43 · Event — Admin manual grant", render_event(
            glyph="fast_up", title="Admin manual grant",
            fields=[("User", who), ("Granted", "<code>30</code> days")],
            extra={"admin_id": _TG, "expires_at": _EXPIRES_SHORT})),
        ("44 · Event — Trial started", render_event(
            glyph="fast_up", title="Trial started",
            fields=[("User", who), ("Trial", "<code>3</code> days")],
            extra={"plan": "main", "expires_at": fmt_dt(_EXPIRES)})),
        ("45 · Event — Referral balance purchase", render_event(
            glyph="check", title="Referral balance purchase",
            fields=[("User", who), ("Paid", money(Decimal("79.00"), "USD"))],
            extra={"tariff": "1m", "plan": "main", "net": "79.00 USD",
                   "paid_from": "referral_balance", "remaining_balance": "12.50 USD",
                   "expires_at": fmt_dt(_EXPIRES)})),
        ("46 · Event — Promo code applied", render_event(
            glyph="tag", title="Promo code applied",
            fields=[("User", who), ("Promo", f"<code>{escape('WELCOME10')}</code>")],
            extra={"kind": PromoKind.percent.value,
                   "value": _fmt_promo_value(PromoKind.percent, Decimal("10")),
                   "uses": "3/100"})),
        ("47 · Event — Subscription access revoked", render_event(
            glyph="no_entry", title="Subscription access revoked",
            fields=[("User", who), ("Reason", "subscription expired")],
            extra={"plan": "main", "expired_at": fmt_dt(_EXPIRES),
                   "revoked_at": fmt_dt(_NOW)})),
        ("48 · Event — Notification run (heartbeat)", render_event(
            glyph="play", title="Subscription notification run",
            fields=[("Trigger", "first run since start")],
            extra={"3d": 4, "0d": 2, "post": 1, "revoked": 1, "errors": 0})),
        ("49 · Event — Broadcast glyph (registry sample, not emitted in code)", render_event(
            glyph="loud", title="Broadcast",
            fields=[("Segment", "active subscribers")],
            extra={"recipients": 128, "sent": 126, "failed": 2})),
    ]
    return [Preview("events", label, text, is_event=True) for label, text in events]


# ---------------------------------------------------------------------------------------
# ADMIN — panel screens (grant, refset, promo builder, broadcast, stats)
# ---------------------------------------------------------------------------------------
def _admin_previews(i18n: JsonI18n, settings: Settings, lang: str) -> list[Preview]:
    g = lambda key, **kw: i18n.gettext(key, lang, **kw)  # noqa: E731
    sample_promos = [
        Promo(id=1, code="WELCOME10", kind=PromoKind.percent, value=Decimal("10"),
              uses=3, max_uses=100),
        Promo(id=2, code="PLUS7", kind=PromoKind.extra_days, value=Decimal("7"),
              uses=0, max_uses=None),
    ]
    stats = StatsSnapshot(
        period=StatsPeriod.d30,
        revenue=RevenueTotals(
            gross=Decimal("4820.00"), net=Decimal("4675.40"), fee=Decimal("144.60"),
            paid_count=61),
        by_provider=[
            ProviderRevenue(provider=InvoiceProvider.cryptobot, gross=Decimal("3160.00"),
                            net=Decimal("3065.20"), paid_count=40),
            ProviderRevenue(provider=InvoiceProvider.xrocket, gross=Decimal("1580.00"),
                            net=Decimal("1580.00"), paid_count=18),
            ProviderRevenue(provider=InvoiceProvider.stars, gross=Decimal("80.00"),
                            net=Decimal("30.20"), paid_count=3),
        ],
        new_users=214,
        active_subscriptions=137,
        top_referrers=[
            TopReferrer(referrer_id=1, tg_id=_TG, username=_USERNAME,
                        earned_usd=Decimal("128.40"), referral_count=12),
            TopReferrer(referrer_id=2, tg_id=222333444, username=None,
                        earned_usd=Decimal("64.10"), referral_count=6),
        ],
        dau=73,
        mau=402,
    )
    return [
        Preview("admin", "50 · Admin — panel root", g("admin.panel.title"),
                root_keyboard(i18n, lang)),
        Preview("admin", "51 · Admin — grant prompt", g("admin.grant.prompt"),
                cancel_keyboard(i18n, lang)),
        Preview("admin", "52 · Admin — grant success",
                g("grant.success", user_id=_TG, days=30, expires_at=_EXPIRES_SHORT),
                back_keyboard(i18n, lang)),
        Preview("admin", "53 · Admin — refset prompt", g("admin.refset.prompt"),
                cancel_keyboard(i18n, lang)),
        Preview("admin", "54 · Admin — refset success",
                g("refset.success", user_id=_TG, percent=15, updated=4),
                back_keyboard(i18n, lang)),
        Preview("admin", "55 · Admin — promo list", g("admin.promo.title"),
                promo_list_keyboard(i18n, lang, sample_promos, page=0, total_pages=1)),
        Preview("admin", "56 · Admin — promo builder: code", g("admin.promo.new.code"),
                cancel_keyboard(i18n, lang)),
        Preview("admin", "57 · Admin — promo builder: kind", g("admin.promo.new.kind"),
                promo_kind_keyboard(i18n, lang)),
        Preview("admin", "58 · Admin — promo builder: value", g("admin.promo.new.value"),
                cancel_keyboard(i18n, lang)),
        Preview("admin", "59 · Admin — promo builder: max uses", g("admin.promo.new.max_uses"),
                skip_cancel_keyboard(i18n, lang)),
        Preview("admin", "60 · Admin — promo builder: expires", g("admin.promo.new.expires"),
                skip_cancel_keyboard(i18n, lang)),
        Preview("admin", "61 · Admin — promo builder: tariffs", g("admin.promo.new.tariffs"),
                promo_tariffs_keyboard(i18n, lang, settings, selected=["1m"])),
        Preview("admin", "62 · Admin — promo builder: confirm",
                g("admin.promo.new.confirm", code="WELCOME10", kind="percent", value="10",
                  max_uses="100", expires="2026-12-31", tariffs="1m, 3m"),
                promo_confirm_keyboard(i18n, lang)),
        Preview("admin", "63 · Admin — promo created / empty (alerts)",
                "• created: " + g("admin.promo.created")
                + "\n• empty: " + g("admin.promo.empty")
                + "\n• deactivated: " + g("admin.promo.deactivated")
                + "\n• duplicate: " + g("admin.promo.duplicate")),
        Preview("admin", "64 · Admin — broadcast segment choice", g("broadcast.choose_segment"),
                _segment_keyboard(i18n, lang)),
        Preview("admin", "65 · Admin — stats screen",
                _format_stats(i18n, lang, stats), stats_keyboard(i18n, lang, active=stats.period)),
        Preview("admin", "66 · Admin — guards & validation (alerts / command replies)",
                "\n".join((
                    "• grant.usage: " + g("grant.usage"),
                    "• grant.forbidden: " + g("grant.forbidden"),
                    "• refset.usage: " + g("refset.usage"),
                    "• refset.forbidden: " + g("refset.forbidden"),
                    "• refset.success.future: "
                    + g("refset.success.future", user_id=_TG, percent=15),
                    "• admin.grant.invalid: " + g("admin.grant.invalid"),
                    "• admin.refset.invalid: " + g("admin.refset.invalid"),
                    "• admin.promo.deact_failed: " + g("admin.promo.deact_failed"),
                    "• admin.promo.new.value_invalid: " + g("admin.promo.new.value_invalid"),
                    "• admin.promo.new.expires_invalid: " + g("admin.promo.new.expires_invalid"),
                    "• broadcast.private_only: " + g("broadcast.private_only"),
                    "• broadcast.forbidden: " + g("broadcast.forbidden"),
                ))),
        Preview("admin", "67 · Admin — scheduled broadcast finished DM (hardcoded ru/en, no ua)",
                _DONE_TEXT.get(lang, _DONE_TEXT["en"]).format(successful=126, total=128, failed=2)),
    ]


_SECTIONS = ("user", "notify", "events", "admin")


def _build_previews(
    i18n: JsonI18n, settings: Settings, lang: str, only: str | None
) -> list[Preview]:
    builders = {
        "user": lambda: _user_previews(i18n, settings, lang),
        "notify": lambda: _notify_previews(i18n, settings, lang),
        "events": _event_previews,
        "admin": lambda: _admin_previews(i18n, settings, lang),
    }
    wanted = _SECTIONS if only is None else (only,)
    previews: list[Preview] = []
    for section in wanted:
        previews.extend(builders[section]())
    return previews


async def _validate_custom_emoji(bot: Bot, settings: Settings) -> None:
    """Mirror bot.__main__ startup: keep only ids Telegram confirms, else fall back to plain."""
    if not settings.bot.custom_emoji_icons:
        disable_icons()
        disable_event_emoji()
        return
    for ids, apply, disable in (
        (configured_icon_ids(), apply_valid_icon_ids, disable_icons),
        (configured_event_emoji_ids(), apply_valid_event_emoji, disable_event_emoji),
    ):
        if not ids:
            continue
        try:
            stickers = await bot.get_custom_emoji_stickers(ids)
        except Exception:  # noqa: BLE001 - degrade to plain glyphs, never abort the preview
            disable()
            continue
        apply({sticker.custom_emoji_id for sticker in stickers})


async def _send_one(bot: Bot, chat_id: int, preview: Preview) -> bool:
    text = _compact_html(preview.text) if preview.is_event else preview.text
    for _ in range(5):
        try:
            if preview.invoice is not None:
                await bot.send_invoice(chat_id=chat_id, **preview.invoice)
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=preview.markup,
                    disable_web_page_preview=preview.is_event,
                )
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 30) + 1)
        except TelegramBadRequest as exc:
            print(f"  ! failed [{preview.label}]: {exc.message}", file=sys.stderr)
            return False
        except Exception as exc:  # noqa: BLE001 - one bad send must not abort the whole run
            print(f"  ! failed [{preview.label}]: {exc}", file=sys.stderr)
            return False
    return False


async def _run(args: argparse.Namespace, *, stdout: TextIO) -> int:
    settings = load_settings()
    i18n = JsonI18n(fallback_language=settings.bot.fallback_language)
    lang = i18n.resolve_language(args.lang)

    chat_id = args.chat_id
    if chat_id is None:
        if not settings.admin_ids:
            stdout.write("error: no admin_ids configured; pass --chat-id\n")
            return 2
        chat_id = sorted(settings.admin_ids)[0]

    previews = _build_previews(i18n, settings, lang, args.only)

    if args.dry_run:
        for preview in previews:
            stdout.write(f"\n=== [{preview.section}] {preview.label} ===\n")
            if preview.invoice is not None:
                inv = preview.invoice
                price = inv["prices"][0]
                stdout.write(
                    f"[invoice card] title={inv['title']!r} desc={inv['description']!r} "
                    f"price={price.amount} {inv['currency']} (label={price.label!r})\n"
                )
                continue
            body = _compact_html(preview.text) if preview.is_event else preview.text
            stdout.write(body + "\n")
            if preview.markup is not None:
                buttons = " | ".join(
                    btn.text for row in preview.markup.inline_keyboard for btn in row
                )
                stdout.write(f"[buttons] {buttons}\n")
        stdout.write(f"\n{len(previews)} previews (dry-run, chat_id={chat_id}, lang={lang})\n")
        return 0

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    sent = 0
    try:
        await _validate_custom_emoji(bot, settings)
        stdout.write(f"Sending {len(previews)} previews to chat {chat_id} (lang={lang})...\n")
        for preview in previews:
            if not args.no_labels:
                await _send_one(bot, chat_id, Preview(preview.section, "", f"▸ {preview.label}"))
                await asyncio.sleep(args.delay)
            if await _send_one(bot, chat_id, preview):
                sent += 1
            stdout.write(f"  [{preview.section}] {preview.label}\n")
            await asyncio.sleep(args.delay)
    finally:
        await bot.session.close()
    stdout.write(f"Done: {sent}/{len(previews)} messages delivered.\n")
    return 0 if sent == len(previews) else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a design preview of every bot message to one chat.",
    )
    parser.add_argument("--chat-id", type=int, help="Target chat (default: first admin id).")
    parser.add_argument("--lang", default="ru", help="Locale: ru | ua | en (default: ru).")
    parser.add_argument("--only", choices=_SECTIONS, help="Send only one section.")
    parser.add_argument("--no-labels", action="store_true", help="Skip separator label messages.")
    parser.add_argument("--dry-run", action="store_true", help="Print previews, do not send.")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between sends.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    return asyncio.run(_run(_parse_args(argv), stdout=stdout))


if __name__ == "__main__":
    raise SystemExit(main())
