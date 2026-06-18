from __future__ import annotations

from datetime import UTC, datetime

from bot.core.i18n import JsonI18n

NOTIFICATION_KEYS = {
    "3d": "notification.3d.text",
    "0d": "notification.0d.text",
    "post": "notification.post.text",
}


def format_subscription_notification(
    i18n: JsonI18n,
    kind: str,
    lang_code: str,
    *,
    expires_at: datetime,
    support_url: str,
) -> str:
    key = NOTIFICATION_KEYS.get(kind)
    if key is None:
        msg = f"unknown notification kind: {kind}"
        raise ValueError(msg)

    return i18n.gettext(
        key,
        lang_code,
        expires_at=_format_datetime(expires_at),
        support_url=support_url,
    )


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
