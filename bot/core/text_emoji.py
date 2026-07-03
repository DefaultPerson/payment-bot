"""Custom-emoji glyphs embedded in message TEXT (welcome, profile, …).

Same mechanism as the service-channel events (see :mod:`bot.core.service_events`): glyphs are
custom emoji embedded in message TEXT via ``<tg-emoji emoji-id="...">fallback</tg-emoji>`` HTML
markup (not the button ``icon_custom_emoji_id`` used by :mod:`bot.keyboards.icons`). Ids are
validated at startup (:func:`bot.__main__.configure_text_emoji`); if validation fails or the
feature is off, :func:`text_emoji` degrades to the plain unicode glyph and the send still works.

The "api"/"free" glyphs come from the CryptachEmoji4 pack; the rest come from the same public
"tgiosicons" pack the other icons use.
"""

from __future__ import annotations

# glyph key -> (plain unicode fallback, custom_emoji_id)
TEXT_EMOJI: dict[str, tuple[str, str]] = {
    "wave": ("\U0001f44b", "6041921818896372382"),  # 👋 tgiosicons
    "new": ("\U0001f195", "5895669571058142797"),  # 🆕 tgiosicons
    "api": ("⚙️", "5287480366330816274"),  # ⚙️ CryptachEmoji4
    "question": ("❓", "6030848053177486888"),  # ❓ tgiosicons
    "profile": ("\U0001f464", "6032994772321309200"),  # 👤 tgiosicons
    "people": ("\U0001f465", "6032609071373226027"),  # 👥 tgiosicons
    "free": ("\U0001f193", "5284976439051954090"),  # 🆓 CryptachEmoji4
    "coin": ("\U0001fa99", "5904462880941545555"),  # 🪙 tgiosicons (same as the buy button)
}

# Active subset after startup validation. Defaults to every configured id so unit tests
# (which never run the validator) still observe the custom-emoji markup.
_active_ids: set[str] = {cid for _, cid in TEXT_EMOJI.values()}


def text_emoji(glyph: str) -> str:
    """Custom-emoji ``<tg-emoji>`` markup for *glyph*, or the plain unicode fallback."""
    fallback, custom_id = TEXT_EMOJI[glyph]
    if custom_id in _active_ids:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback


def configured_text_emoji_ids() -> list[str]:
    """All distinct custom_emoji_ids referenced by the text-emoji registry."""
    return sorted({cid for _, cid in TEXT_EMOJI.values()})


def apply_valid_text_emoji(valid_ids: set[str]) -> None:
    """Keep only ids Telegram confirmed valid; others fall back to plain unicode."""
    _active_ids.clear()
    _active_ids.update(valid_ids)


def disable_text_emoji() -> None:
    """Drop all custom emoji (validation failed or the feature is switched off)."""
    _active_ids.clear()
