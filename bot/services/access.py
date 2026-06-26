from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Protocol

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.repos.tariffs import get_plan

INVITE_LINK_NAME_LIMIT = 32


def allowed_chat_ids_for_plan(settings: Settings, plan_code: str | None) -> set[int]:
    """Chats a subscription on ``plan_code`` may enter.

    A plan with an empty ``chat_ids`` (and an unknown/None plan) opens every configured
    protected chat — the current single-plan behaviour. A plan that lists chats is
    restricted to that subset.
    """
    all_chats = {chat.chat_id for chat in settings.access.protected_chats}
    if plan_code is None:
        return all_chats
    try:
        plan = get_plan(settings, plan_code)
    except LookupError:
        return all_chats
    if not plan.chat_ids:
        return all_chats
    return set(plan.chat_ids)


class SupportsAccessBot(Protocol):
    async def create_chat_invite_link(
        self,
        *,
        chat_id: int,
        name: str,
        expire_date: datetime,
        creates_join_request: bool,
    ) -> object: ...

    async def send_message(self, *, chat_id: int, text: str) -> object: ...

    async def ban_chat_member(
        self,
        *,
        chat_id: int,
        user_id: int,
        until_date: datetime,
        revoke_messages: bool,
    ) -> object: ...

    async def unban_chat_member(
        self,
        *,
        chat_id: int,
        user_id: int,
        only_if_banned: bool,
    ) -> object: ...


class SupportsInviteLink(Protocol):
    invite_link: str


@dataclass(frozen=True, slots=True)
class AccessInvite:
    title: str
    invite_link: str


async def create_protected_chat_invite_links(
    bot: SupportsAccessBot,
    settings: Settings,
    *,
    now: datetime,
) -> list[AccessInvite]:
    expire_date = now + timedelta(seconds=settings.access.invite_link_ttl_seconds)
    invites: list[AccessInvite] = []
    for chat in settings.access.protected_chats:
        # Join-request links (no member_limit — Telegram forbids combining the two).
        # Entry is authorized per joining tg_id by the chat_join_request handler, not by
        # trusting a forwardable link, so a shared link can no longer grant access.
        invite = await bot.create_chat_invite_link(
            chat_id=chat.chat_id,
            name=_invite_name(chat.title),
            expire_date=expire_date,
            creates_join_request=True,
        )
        invites.append(AccessInvite(title=chat.title, invite_link=_invite_link(invite)))
    return invites


async def send_access_links(
    bot: SupportsAccessBot,
    settings: Settings,
    i18n: JsonI18n,
    *,
    tg_id: int,
    lang_code: str,
    now: datetime,
) -> None:
    if not settings.access.protected_chats:
        await bot.send_message(
            chat_id=tg_id,
            text=i18n.gettext(
                "access.empty.text",
                lang_code,
                support_url=_support_url(settings),
            ),
        )
        return

    invites = await create_protected_chat_invite_links(bot, settings, now=now)
    links = "\n".join(
        f"{escape(invite.title)}: {escape(invite.invite_link)}" for invite in invites
    )
    await bot.send_message(
        chat_id=tg_id,
        text=i18n.gettext("access.links.text", lang_code, links=links),
    )


async def revoke_protected_chat_access(
    bot: SupportsAccessBot,
    settings: Settings,
    *,
    tg_id: int,
    now: datetime,
) -> None:
    # Expiry removes the user's only subscription, so they lose access entirely — revoke
    # from EVERY protected chat. Per-plan scoping is intentionally NOT applied here: the
    # ban+unban pair is a harmless no-op for a non-member, and scoping the revoke would
    # orphan chats a user had joined under a previous/broader plan.
    until_date = now + timedelta(seconds=settings.access.revoke_ban_seconds)
    for chat in settings.access.protected_chats:
        await bot.ban_chat_member(
            chat_id=chat.chat_id,
            user_id=tg_id,
            until_date=until_date,
            revoke_messages=False,
        )
        await bot.unban_chat_member(
            chat_id=chat.chat_id,
            user_id=tg_id,
            only_if_banned=True,
        )


def _invite_link(invite: SupportsInviteLink | dict[str, object]) -> str:
    if isinstance(invite, dict):
        return str(invite["invite_link"])
    return str(invite.invite_link)


def _invite_name(title: str) -> str:
    return title[:INVITE_LINK_NAME_LIMIT]


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"
