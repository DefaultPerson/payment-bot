"""Guard: every locale string must be valid Telegram HTML.

The bot sends all messages with ``DefaultBotProperties(parse_mode=HTML)``, so a literal
``<`` in a locale value makes Telegram try to parse an HTML entity and reject the send with
``TelegramBadRequest: can't parse entities`` — which, on the usage/prompt path, silently
killed the ``/broadcast`` (and ``/grant`` /``/refset`` /``/promo``) handlers. Literal angle
brackets from dynamic content must be escaped (``&lt;`` / ``&gt;``). A small safelist of
intentional formatting tags (``<b>``, ``<i>``, ``<tg-emoji …>``) is allowed, since those are
valid Telegram HTML; everything else must remain escaped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).resolve().parents[1] / "bot" / "locales"
_LOCALE_ITEMS = [
    (path.name, key, value)
    for path in sorted(LOCALES_DIR.glob("*.json"))
    for key, value in json.loads(path.read_text(encoding="utf-8")).items()
]

# Intentional, valid Telegram-HTML formatting tags allowed in locale templates.
_ALLOWED_TAGS = re.compile(r"</?(?:b|i|u|s|tg-emoji)(?:\s[^>]*)?>")


@pytest.mark.parametrize(("filename", "key", "value"), _LOCALE_ITEMS)
def test_locale_value_has_no_raw_angle_bracket(filename: str, key: str, value: str) -> None:
    stripped = _ALLOWED_TAGS.sub("", value)
    assert "<" not in stripped, (
        f"{filename}:{key} contains a raw '<' which breaks HTML parse_mode; "
        f"escape it as '&lt;' / '&gt;'. Value: {value!r}"
    )
