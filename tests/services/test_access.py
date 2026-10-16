from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.services.access import AccessInvite, create_protected_chat_invite_links, send_access_links


class FakeBot:
    def __init__(self, *, fail_approve: bool = False) -> None:
        self.invite_calls: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.bans: list[dict[str, Any]] = []
        self.unbans: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.fail_approve = fail_approve

    async def create_chat_invite_link(self, **kwargs: Any) -> SimpleNamespace:
        self.invite_calls.append(kwargs)
        return SimpleNamespace(invite_link=f"https://t.me/+{kwargs['chat_id']}")

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)

    async def ban_chat_member(self, **kwargs: Any) -> None:
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: Any) -> None:
        self.unbans.append(kwargs)

    async def approve_chat_join_request(self, **kwargs: Any) -> None:
        # Telegram raises when there is no pending request / the user is already a member.
        if self.fail_approve:
            raise RuntimeError("no pending join request")
        self.approvals.append(kwargs)


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
    assert "<b>Signals</b>: https://t.me/+-1001" in message["text"]
    assert "<b>Research</b>: https://t.me/+-1002" in message["text"]


async def test_send_access_links_attaches_dismiss_button() -> None:
    from bot.keyboards import CB_DISMISS

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

    keyboard = bot.messages[0]["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == CB_DISMISS


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

    assert (
        "<b>Signals &lt;VIP&gt; &amp; Research</b>: https://t.me/+-1001"
        in bot.messages[0]["text"]
    )
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


def _two_plan_settings() -> Settings:
    # default (first active) plan "base" opens only -1001; "vip" opens only -1002.
    return Settings(
        bot={"support_username": "HydraSupport"},
        access={
            "invite_link_ttl_seconds": 600,
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
        tariffs={
            "plans": [
                {"code": "base", "chat_ids": [-1001]},
                {"code": "vip", "chat_ids": [-1002]},
            ],
            "items": [
                {
                    "code": "base_1m",
                    "plan_code": "base",
                    "duration_months": 1,
                    "base_price_usd": "59",
                },
                {
                    "code": "vip_1m",
                    "plan_code": "vip",
                    "duration_months": 1,
                    "base_price_usd": "99",
                },
            ],
        },
    )


def test_allowed_chats_unknown_or_none_plan_falls_back_to_default_not_all() -> None:
    from bot.services.access import allowed_chat_ids_for_plan

    settings = _two_plan_settings()

    # None / retired plan code must resolve to the default (base) plan, NOT open every chat.
    assert allowed_chat_ids_for_plan(settings, None) == {-1001}
    assert allowed_chat_ids_for_plan(settings, "retired") == {-1001}
    assert allowed_chat_ids_for_plan(settings, "vip") == {-1002}


async def test_send_access_links_reconciles_membership_to_plan() -> None:
    bot = FakeBot()
    settings = _two_plan_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
        plan_code="vip",
    )

    # vip opens only -1002: kick from -1001 and only offer the -1002 link.
    assert [call["chat_id"] for call in bot.bans] == [-1001]
    assert [call["chat_id"] for call in bot.unbans] == [-1001]
    assert [call["chat_id"] for call in bot.invite_calls] == [-1002]
    assert "<b>Research</b>: https://t.me/+-1002" in bot.messages[0]["text"]
    assert "Signals" not in bot.messages[0]["text"]


async def test_send_access_links_survives_unkickable_owner() -> None:
    # Regression: a user who OWNS a disallowed chat cannot be kicked (Telegram replies
    # "Bad Request: can't remove chat owner"). The kick is a best-effort reconciliation and
    # must not abort access-link delivery — a paid user still gets their links instead of a
    # generic error.
    class OwnerBot(FakeBot):
        async def ban_chat_member(self, **kwargs: Any) -> None:
            raise RuntimeError("Bad Request: can't remove chat owner")

    bot = OwnerBot()
    settings = _two_plan_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    # Must NOT raise even though the kick from -1001 (which the user owns) fails.
    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
        plan_code="vip",
    )

    # Ban raised before recording; unban is skipped; the allowed chat's link is still delivered.
    assert bot.bans == []
    assert bot.unbans == []
    assert [call["chat_id"] for call in bot.invite_calls] == [-1002]
    assert "<b>Research</b>: https://t.me/+-1002" in bot.messages[0]["text"]


async def test_approve_pending_join_requests_approves_only_plan_chats() -> None:
    from bot.services.access import approve_pending_join_requests

    bot = FakeBot()
    settings = _two_plan_settings()

    await approve_pending_join_requests(bot, settings, tg_id=1001, plan_code="vip")

    # vip unlocks only -1002 -> approve any pending request there, addressed by tg_id.
    assert bot.approvals == [{"chat_id": -1002, "user_id": 1001}]


async def test_approve_pending_join_requests_swallows_missing_request() -> None:
    from bot.services.access import approve_pending_join_requests

    bot = FakeBot(fail_approve=True)
    settings = make_settings()

    # No pending request (Telegram raises) must not propagate.
    await approve_pending_join_requests(bot, settings, tg_id=1001, plan_code="main")

    assert bot.approvals == []


async def test_send_access_links_approves_pending_for_allowed_chats() -> None:
    bot = FakeBot()
    settings = _two_plan_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
        plan_code="vip",
    )

    # A user who requested before paying is admitted on payment for their plan's chats.
    assert bot.approvals == [{"chat_id": -1002, "user_id": 1001}]


async def test_send_access_links_single_plan_does_not_kick_and_offers_all() -> None:
    bot = FakeBot()
    settings = make_settings()  # single default plan, chat_ids=[] -> opens all chats
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
        plan_code="main",
    )

    assert bot.bans == []
    assert [call["chat_id"] for call in bot.invite_calls] == [-1001, -1002]


def test_access_and_payment_locale_keys_are_available() -> None:
    i18n = JsonI18n(fallback_language="en")

    for language in ("en", "ru", "ua"):
        links_text = i18n.gettext(
            "access.links.text", language, sparkles="✨", links="Signals: link"
        )
        empty_text = i18n.gettext(
            "access.empty.text",
            language,
            support_url="https://t.me/HydraSupport",
        )
        assert links_text != "access.links.text"
        assert "Signals: link" in links_text
        assert empty_text != "access.empty.text"
        assert "https://t.me/HydraSupport" in empty_text


async def test_send_access_links_edits_in_place_with_main_menu_button() -> None:
    from bot.keyboards import CB_MENU

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
        edit_message_id=555,  # button-triggered (enter_chat): edit in place
    )

    assert bot.messages == []  # no new message
    assert len(bot.edits) == 1
    assert bot.edits[0]["chat_id"] == 1001
    assert bot.edits[0]["message_id"] == 555
    assert "<b>Signals</b>: https://t.me/+-1001" in bot.edits[0]["text"]
    # Edited path carries a "Main menu" button (navigation stays in one message).
    assert bot.edits[0]["reply_markup"].inline_keyboard[0][0].callback_data == CB_MENU


async def test_send_access_links_falls_back_to_new_message_when_edit_fails() -> None:
    class FailingEditBot(FakeBot):
        async def edit_message_text(self, **kwargs: Any) -> None:
            raise RuntimeError("message can't be edited")

    bot = FailingEditBot()
    settings = make_settings()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    await send_access_links(
        bot,
        settings,
        JsonI18n(fallback_language="en"),
        tg_id=1001,
        lang_code="en",
        now=now,
        edit_message_id=555,
    )

    # Edit failed → a new message is sent instead (links are never lost).
    assert len(bot.messages) == 1
    assert "<b>Signals</b>: https://t.me/+-1001" in bot.messages[0]["text"]
