from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.core import service_events as se


@pytest.fixture(autouse=True)
def _restore_active_emoji() -> object:
    # Tests toggle the active-id set; restore the default afterwards.
    saved = set(se._active_ids)
    yield
    se._active_ids.clear()
    se._active_ids.update(saved)


def test_render_event_matches_layout() -> None:
    text = se.render_event(
        glyph="coin",
        title="Cryptobot payment paid",
        fields=[
            ("User", se.user_mention(123456789, "example_user")),
            ("Payment", se.money(Decimal("69.12"), "USDT")),
        ],
        extra={
            "provider_invoice_id": "TEST-CB-0001",
            "tariff": "1m",
            "gross": "79.00 USDT",
            "expires_at": "2026-07-25 09:54 UTC",
        },
    )
    assert text == (
        '<tg-emoji emoji-id="5904462880941545555">\U0001fa99</tg-emoji>'
        " <b>Cryptobot payment paid</b>\n"
        "\n"
        "<b>User:</b> @example_user // <code>123456789</code>\n"
        "<b>Payment:</b> <code>69.12</code> <code>USDT</code>\n"
        "\n"
        "<b>Additional Info</b>\n"
        "<blockquote>provider_invoice_id=TEST-CB-0001\n"
        "tariff=1m\n"
        "gross=79.00 USDT\n"
        "expires_at=2026-07-25 09:54 UTC</blockquote>"
    )


def test_user_mention_variants() -> None:
    assert se.user_mention(42, "alice") == "@alice // <code>42</code>"
    assert se.user_mention(42, "@alice") == "@alice // <code>42</code>"  # leading @ trimmed
    assert se.user_mention(42, None) == "<code>42</code>"
    assert se.user_mention(None, "alice") == "—"


def test_money_and_amount_formatting() -> None:
    assert se.money(Decimal("5"), "USDT") == "<code>5.00</code> <code>USDT</code>"
    assert se.money(None, "XTR") == "<code>unknown</code> <code>XTR</code>"
    assert se.fmt_amount(Decimal("76.6")) == "76.60"
    assert se.fmt_amount(None) == "unknown"


def test_fmt_dt_normalizes_to_utc() -> None:
    naive = datetime(2026, 7, 25, 9, 54)
    assert se.fmt_dt(naive) == "2026-07-25 09:54 UTC"
    assert se.fmt_dt(datetime(2026, 7, 25, 9, 54, tzinfo=UTC)) == "2026-07-25 09:54 UTC"
    assert se.fmt_dt(None) == "unknown"


def test_emoji_html_falls_back_to_plain_unicode_when_disabled() -> None:
    assert se.emoji_html("star").startswith("<tg-emoji")
    se.disable_event_emoji()
    assert se.emoji_html("star") == "⭐️"


def test_emoji_html_uses_custom_only_for_validated_ids() -> None:
    se.apply_valid_event_emoji({se.EVENT_EMOJI["coin"][1]})
    assert se.emoji_html("coin").startswith("<tg-emoji")
    assert se.emoji_html("cross") == "❌"  # not in the validated set -> plain glyph


def test_new_event_glyphs_use_pack_custom_emoji() -> None:
    # tag (🏷 promo applied) and no_entry (🚫 access revoked) are real tgiosicons ids,
    # so they render as custom emoji and take part in startup validation like the rest.
    for glyph in ("tag", "no_entry"):
        fallback, custom_id = se.EVENT_EMOJI[glyph]
        assert custom_id.isdigit()
        assert custom_id in se.configured_event_emoji_ids()
        assert se.emoji_html(glyph) == f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'


def test_html_is_escaped_in_extra_and_mention() -> None:
    text = se.render_event(
        glyph="cross",
        title="Join request declined",
        fields=[("User", se.user_mention(7, "a<b>d"))],
        extra={"note": "x & y <z>"},
    )
    assert "@a&lt;b&gt;d" in text
    assert "note=x &amp; y &lt;z&gt;" in text
