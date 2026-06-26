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
        self.bans: list[dict[str, Any]] = []
        self.unbans: list[dict[str, Any]] = []

    async def create_chat_invite_link(self, **kwargs: Any) -> SimpleNamespace:
        self.invite_calls.append(kwargs)
        return SimpleNamespace(invite_link=f"https://t.me/+{kwargs['chat_id']}")

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)

    async def ban_chat_member(self, **kwargs: Any) -> None:
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: Any) -> None:
        self.unbans.append(kwargs)


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


async def test_create_invite_links_uses_join_request_ttl() -> None:
    bot = FakeBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    links = await create_protected_chat_invite_links(bot, settings, now=now)

    assert links[0] == AccessInvite(title="Signals", invite_link="https://t.me/+-1001")
    # Join-request link, no member_limit: entry is authorized per joining tg_id, not by
    # the link itself (Telegram also forbids member_limit together with join requests).
    assert bot.invite_calls[0] == {
        "chat_id": -1001,
        "name": "Signals",
        "expire_date": now + timedelta(seconds=600),
        "creates_join_request": True,
    }


async def test_create_invite_links_preserves_multiple_chat_order() -> None:
    bot = FakeBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    links = await create_protected_chat_invite_links(bot, settings, now=now)

    assert [link.title for link in links] == ["Signals", "Research"]
    assert [call["chat_id"] for call in bot.invite_calls] == [-1001, -1002]


async def test_create_invite_links_truncates_long_invite_names() -> None:
    bot = FakeBot()
    settings = Settings(
        access={
            "protected_chats": [
                {
                    "chat_id": -1001,
                    "title": "A very long protected chat title beyond telegram limit",
                }
            ],
        },
    )
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await create_protected_chat_invite_links(bot, settings, now=now)

    assert len(bot.invite_calls[0]["name"]) <= 32
    assert bot.invite_calls[0]["name"] == "A very long protected chat title"[:32]


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


async def test_send_access_links_escapes_html_sensitive_titles() -> None:
    bot = FakeBot()
    settings = Settings(
        access={
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals <VIP> & Research"},
            ],
        },
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

    assert "Signals &lt;VIP&gt; &amp; Research: https://t.me/+-1001" in bot.messages[0]["text"]
    assert "Signals <VIP> & Research: https://t.me/+-1001" not in bot.messages[0]["text"]


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


async def test_revoke_protected_chat_access_temp_bans_then_unbans() -> None:
    from bot.services.access import revoke_protected_chat_access

    bot = FakeBot()
    settings = Settings(
        access={
            "revoke_ban_seconds": 60,
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
    )
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await revoke_protected_chat_access(bot, settings, tg_id=1001, now=now)

    assert [call["chat_id"] for call in bot.bans] == [-1001, -1002]
    assert bot.bans[0]["user_id"] == 1001
    assert bot.bans[0]["until_date"] == now + timedelta(seconds=60)
    assert bot.bans[0]["revoke_messages"] is False
    assert [call["chat_id"] for call in bot.unbans] == [-1001, -1002]
    assert bot.unbans[0]["user_id"] == 1001
    assert bot.unbans[0]["only_if_banned"] is True


async def test_revoke_scopes_bans_to_plan_chats() -> None:
    from bot.services.access import revoke_protected_chat_access

    bot = FakeBot()
    settings = Settings(
        access={
            "revoke_ban_seconds": 60,
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
        tariffs={
            "plans": [{"code": "vip", "chat_ids": [-1002]}],
            "items": [
                {
                    "code": "vip_1m",
                    "plan_code": "vip",
                    "duration_months": 1,
                    "base_price_usd": "59",
                }
            ],
        },
    )
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await revoke_protected_chat_access(bot, settings, tg_id=1001, now=now, plan_code="vip")

    # The vip plan opens only -1002, so revocation must not touch -1001.
    assert [call["chat_id"] for call in bot.bans] == [-1002]
    assert [call["chat_id"] for call in bot.unbans] == [-1002]


async def test_revoke_without_plan_code_bans_all_protected_chats() -> None:
    from bot.services.access import revoke_protected_chat_access

    bot = FakeBot()
    settings = Settings(
        access={
            "revoke_ban_seconds": 60,
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
    )
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await revoke_protected_chat_access(bot, settings, tg_id=1001, now=now)

    assert [call["chat_id"] for call in bot.bans] == [-1001, -1002]


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
