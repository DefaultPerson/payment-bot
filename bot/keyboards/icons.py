"""Custom-emoji icons for inline-keyboard buttons.

Icons come from the public "tgiosicons" pack (https://t.me/addemoji/tgiosicons) and
are attached to buttons via ``InlineKeyboardButton.icon_custom_emoji_id`` (Bot API 9.4+).
A bot whose owner has Telegram Premium is allowed to render custom emoji; for an
ineligible bot Telegram simply omits the icon (the send still succeeds).

Each id is validated against ``getCustomEmojiStickers`` at startup
(:func:`bot.__main__.configure_menu_icons`). An unknown id is dropped rather than left on a
button, because a stale ``custom_emoji_id`` raises ``CUSTOM_EMOJI_INVALID`` and would fail the
*whole* message send, not just the icon. The map is keyed by the button's i18n text key.
"""

from __future__ import annotations

# i18n text key -> custom_emoji_id (trailing comment is the plain emoji it depicts).
MENU_ICONS: dict[str, str] = {
    "menu.button.buy": "5904462880941545555",  # 🪙 subscription (2nd coin variation)
    "menu.button.trial": "5773677501825945508",  # 🎁 free trial (shared with referral gift)
    "menu.button.profile": "6032994772321309200",  # 👤
    "menu.button.language": "5776233299424843260",  # 🌐
    "menu.button.docs": "6030687898141987254",  # 🧭 documentation link
    "language.check": "5774022692642492953",  # ✅ selected-language marker
    "menu.button.referral": "5773677501825945508",  # 🎁
    "menu.button.promo": "5886285355279193209",  # 🏷 (2nd variation)
    "menu.button.support": "6030622631818956594",  # 💬 main-menu support (2nd variation)
    "menu.button.admin": "5904258298764334001",  # ⚙️
    "profile.button.renew": "5769248574499983619",  # 🔄
    "profile.button.enter_chat": "6035130900075777681",  # 🚪
    "access.button.dismiss": "5774077015388852135",  # ❌ close/dismiss button (2nd variation)
    "nav.back": "5960671702059848143",  # ⬅️ step-back button (1st variation)
    "nav.home": "5938537205847822613",  # 🏡 return to the main menu
    "nav.cancel": "6030757850274336631",  # ❌ cancel button
    "profile.button.support": "6030622631818956594",  # 💬 unified support icon
    "support.button": "6030622631818956594",  # 💬 unified support icon
    "buy.tariff.button": "5890937706803894250",  # 📅
    "buy.pay.cryptobot": "5778613750688911681",  # 🪙
    "buy.pay.stars": "6028338546736107668",  # ⭐️
    "buy.pay.xrocket": "5776023601941582822",  # 💎
    "buy.pay.tribute": "5778421276024509124",  # 💰
    "buy.pay.referral_balance": "5773677501825945508",  # 🎁
}

# Active subset after startup validation. Defaults to all configured icons so unit tests
# (which never run the validator) still observe them attached.
_active: dict[str, str] = dict(MENU_ICONS)


def icon_for(key: str | None) -> str | None:
    """Return the active custom_emoji_id for a button key, or None."""
    if key is None:
        return None
    return _active.get(key)


def configured_icon_ids() -> list[str]:
    """All distinct custom_emoji_ids referenced by the icon map."""
    return sorted(set(MENU_ICONS.values()))


def apply_valid_icon_ids(valid_ids: set[str]) -> None:
    """Keep only icons whose custom_emoji_id Telegram confirmed as valid."""
    _active.clear()
    _active.update({key: cid for key, cid in MENU_ICONS.items() if cid in valid_ids})


def disable_icons() -> None:
    """Drop all icons (validation failed or the feature is switched off)."""
    _active.clear()
