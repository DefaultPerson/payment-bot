"""Custom-emoji glyphs for the /start welcome message text.

Same mechanism as the service-channel events (see :mod:`bot.core.service_events`): glyphs are
custom emoji embedded in message TEXT via ``<tg-emoji emoji-id="...">fallback</tg-emoji>`` HTML
markup (not the button ``icon_custom_emoji_id`` used by :mod:`bot.keyboards.icons`). Ids are
validated at startup (:func:`bot.__main__.configure_welcome_emoji`); if validation fails or the
feature is off, :func:`welcome_emoji` degrades to the plain unicode glyph and the send still works.

The "api" glyph comes from the CryptachEmoji4 pack (https://t.me/addemoji/CryptachEmoji4); the
rest come from the same public "tgiosicons" pack the other icons use.
"""

from __future__ import annotations

# glyph key -> (plain unicode fallback, custom_emoji_id)
WELCOME_EMOJI: dict[str, tuple[str, str]] = {
    "wave": ("\U0001f44b", "6041921818896372382"),  # 👋 tgiosicons
    "new": ("\U0001f195", "5895669571058142797"),  # 🆕 tgiosicons
    "api": ("⚙️", "5287480366330816274"),  # ⚙️ CryptachEmoji4
    "question": ("❓", "6030848053177486888"),  # ❓ tgiosicons
}

# Active subset after startup validation. Defaults to every configured id so unit tests
# (which never run the validator) still observe the custom-emoji markup.
_active_ids: set[str] = {cid for _, cid in WELCOME_EMOJI.values()}


def welcome_emoji(glyph: str) -> str:
    """Custom-emoji ``<tg-emoji>`` markup for *glyph*, or the plain unicode fallback."""
    fallback, custom_id = WELCOME_EMOJI[glyph]
    if custom_id in _active_ids:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback


def configured_welcome_emoji_ids() -> list[str]:
    """All distinct custom_emoji_ids referenced by the welcome registry."""
    return sorted({cid for _, cid in WELCOME_EMOJI.values()})


def apply_valid_welcome_emoji(valid_ids: set[str]) -> None:
    """Keep only ids Telegram confirmed valid; others fall back to plain unicode."""
    _active_ids.clear()
    _active_ids.update(valid_ids)


def disable_welcome_emoji() -> None:
    """Drop all custom emoji (validation failed or the feature is switched off)."""
    _active_ids.clear()
