from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.core.i18n import JsonI18n

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("3d", "expires soon"),
        ("0d", "has expired"),
        ("post", "Access has been revoked"),
    ],
)
def test_format_subscription_notification_uses_localized_text(
    kind: str,
    expected: str,
) -> None:
    from bot.services.notifications import format_subscription_notification

    text = format_subscription_notification(
        JsonI18n(fallback_language="en"),
        kind,
        "en",
        expires_at=NOW,
        support_url="https://t.me/HydraSupport",
    )

    assert expected in text
    assert "2026-06-18 12:00 UTC" in text
    assert "https://t.me/HydraSupport" in text


def test_format_subscription_notification_rejects_unknown_kind() -> None:
    from bot.services.notifications import format_subscription_notification

    with pytest.raises(ValueError, match="unknown notification kind"):
        format_subscription_notification(
            JsonI18n(fallback_language="en"),
            "bad",
            "en",
            expires_at=NOW,
            support_url="https://t.me/HydraSupport",
        )
