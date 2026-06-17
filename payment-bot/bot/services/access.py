from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Protocol

from bot.config import Settings
from bot.core.i18n import JsonI18n

INVITE_LINK_NAME_LIMIT = 32


class SupportsAccessBot(Protocol):
    async def create_chat_invite_link(
        self,
        *,
        chat_id: int,
        name: str,
        expire_date: datetime,
        member_limit: int,
        creates_join_request: bool,
    ) -> object: ...

    async def send_message(self, *, chat_id: int, text: str) -> object: ...


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
        invite = await bot.create_chat_invite_link(
            chat_id=chat.chat_id,
            name=_invite_name(chat.title),
            expire_date=expire_date,
            member_limit=1,
            creates_join_request=False,
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


def _invite_link(invite: SupportsInviteLink | dict[str, object]) -> str:
    if isinstance(invite, dict):
        return str(invite["invite_link"])
    return str(invite.invite_link)


def _invite_name(title: str) -> str:
    return title[:INVITE_LINK_NAME_LIMIT]


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"
