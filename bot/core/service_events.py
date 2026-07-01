"""Rendering for service-channel event messages.

Every service-channel post shares one visual shape: a custom-emoji glyph + bold
title, a short block of the headline facts, then the remaining details folded into
an expandable blockquote ("Additional Info"). Telegram renders the blockquote
collapsed by default, keeping the channel scannable.

Glyphs are custom emoji from the same public "tgiosicons" pack the inline-keyboard
icons use (see :mod:`bot.keyboards.icons`). Ids are validated at startup
(:func:`bot.__main__.configure_event_emoji`); if validation fails or the feature is
off, :func:`emoji_html` degrades to the plain unicode glyph and the send still works.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from html import escape

# glyph key -> (plain unicode fallback, tgiosicons custom_emoji_id)
EVENT_EMOJI: dict[str, tuple[str, str]] = {
    # 🪙 coin, 2nd tgiosicons variation — shared by both crypto payment providers
    "coin": ("\U0001fa99", "5904462880941545555"),  # 🪙 cryptobot + xrocket
    "star": ("⭐️", "6028338546736107668"),  # ⭐️ telegram stars
    "fast_up": ("⏫", "5938437708635443119"),  # ⏫ manual grant
    "loud": ("\U0001f4e2", "6021418126061605425"),  # 📢 broadcast
    "play": ("▶️", "5773626993010546707"),  # ▶️ notification run
    "check": ("✅", "5774022692642492953"),  # ✅ approved / success
    "tag": ("\U0001f3f7", "5888620056551625531"),  # 🏷 promo code applied (pack promo icon)
    "no_entry": ("\U0001f6ab", "5938215362473496448"),  # 🚫 subscription expired / access revoked
}

# Active subset after startup validation. Defaults to every configured id so unit
# tests (which never run the validator) still observe the custom-emoji markup.
_active_ids: set[str] = {cid for _, cid in EVENT_EMOJI.values()}


def emoji_html(glyph: str) -> str:
    """Custom-emoji ``<tg-emoji>`` markup for *glyph*, or the plain unicode fallback."""
    fallback, custom_id = EVENT_EMOJI[glyph]
    if custom_id in _active_ids:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback


def configured_event_emoji_ids() -> list[str]:
    """All distinct custom_emoji_ids referenced by the event registry."""
    return sorted({cid for _, cid in EVENT_EMOJI.values()})


def apply_valid_event_emoji(valid_ids: set[str]) -> None:
    """Keep only ids Telegram confirmed valid; others fall back to plain unicode."""
    _active_ids.clear()
    _active_ids.update(valid_ids)


def disable_event_emoji() -> None:
    """Drop all custom emoji (validation failed or the feature is switched off)."""
    _active_ids.clear()


def render_event(
    *,
    glyph: str,
    title: str,
    fields: list[tuple[str, str]] | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    """Render one service-channel event.

    ``fields`` values are trusted HTML (built via :func:`user_mention` / :func:`money`);
    labels are escaped. ``extra`` becomes ``key=value`` lines inside an expandable
    blockquote, fully escaped.
    """
    lines = [f"{emoji_html(glyph)} <b>{escape(title)}</b>"]
    if fields:
        lines.append("")
        lines.extend(f"<b>{escape(label)}:</b> {value}" for label, value in fields)
    if extra:
        body = "\n".join(f"{escape(str(key))}={escape(str(value))}" for key, value in extra.items())
        lines.extend(("", "<b>Additional Info</b>", f"<blockquote>{body}</blockquote>"))
    return "\n".join(lines)


def user_mention(tg_id: int | None, username: str | None) -> str:
    """``@username // <code>id</code>`` when a username is known, else ``<code>id</code>``."""
    if tg_id is None:
        return "—"  # —
    if username:
        handle = username[1:] if username.startswith("@") else username
        return f"@{escape(handle)} // <code>{tg_id}</code>"
    return f"<code>{tg_id}</code>"


def money(amount: Decimal | None, currency: str | None) -> str:
    """Headline amount as two tap-to-copy ``<code>`` spans."""
    return f"<code>{escape(fmt_amount(amount))}</code> <code>{escape(str(currency))}</code>"


def fmt_amount(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else "unknown"


def fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
