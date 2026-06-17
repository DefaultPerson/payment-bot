from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.services.access import AccessInvite, create_protected_chat_invite_links, send_access_links


class FakeBot:
    def __init__(self) -> None:
        self.invite_calls: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    async def create_chat_invite_link(self, **kwargs: Any) -> SimpleNamespace:
        self.invite_calls.append(kwargs)
        return SimpleNamespace(invite_link=f"https://t.me/+{kwargs['chat_id']}")

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


def make_settings() -> Settings:
    return Settings(
        bot={"support_username": "HydraSupport"},
        access={
            "invite_link_ttl_seconds": 600,
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
    )


async def test_create_invite_links_uses_single_use_ttl() -> None:
    bot = FakeBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    links = await create_protected_chat_invite_links(bot, settings, now=now)

    assert links[0] == AccessInvite(title="Signals", invite_link="https://t.me/+-1001")
    assert bot.invite_calls[0] == {
        "chat_id": -1001,
        "name": "Signals",
        "expire_date": now + timedelta(seconds=600),
        "member_limit": 1,
        "creates_join_request": False,
    }


async def test_create_invite_links_preserves_multiple_chat_order() -> None:
    bot = FakeBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    links = await create_protected_chat_invite_links(bot, settings, now=now)

    assert [link.title for link in links] == ["Signals", "Research"]
    assert [call["chat_id"] for call in bot.invite_calls] == [-1001, -1002]


async def test_send_access_links_sends_localized_links() -> None:
    bot = FakeBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
    )

    assert len(bot.messages) == 1
    message = bot.messages[0]
    assert message["chat_id"] == 1001
    assert "Signals: https://t.me/+-1001" in message["text"]
    assert "Research: https://t.me/+-1002" in message["text"]


async def test_send_access_links_handles_empty_config() -> None:
    bot = FakeBot()
    settings = Settings(
        bot={"support_username": "HydraSupport"},
        access={"protected_chats": []},
    )
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
    )

    assert bot.invite_calls == []
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == 1001
    assert "https://t.me/HydraSupport" in bot.messages[0]["text"]


def test_access_and_payment_locale_keys_are_available() -> None:
    i18n = JsonI18n(fallback_language="en")

    for language in ("en", "ru", "ua"):
        links_text = i18n.gettext("access.links.text", language, links="Signals: link")
        empty_text = i18n.gettext(
            "access.empty.text",
            language,
            support_url="https://t.me/HydraSupport",
        )
        success_text = i18n.gettext("payment.success.text", language)

        assert links_text != "access.links.text"
        assert "Signals: link" in links_text
        assert empty_text != "access.empty.text"
        assert "https://t.me/HydraSupport" in empty_text
        assert success_text != "payment.success.text"
