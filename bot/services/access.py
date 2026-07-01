from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Protocol

from bot.config import PlanConfig, ProtectedChatConfig, Settings
from bot.core.i18n import JsonI18n
from bot.repos.tariffs import default_plan, get_plan

logger = logging.getLogger(__name__)

INVITE_LINK_NAME_LIMIT = 32


def _resolve_plan_or_default(settings: Settings, plan_code: str | None) -> PlanConfig | None:
    """Resolve ``plan_code`` to a plan, falling back to the default plan when it is None or
    unknown (a retired/renamed code). This is fail-SAFE: an unresolvable plan grants the
    default plan's access, never blanket access to every chat."""
    if plan_code is not None:
        try:
            return get_plan(settings, plan_code)
        except LookupError:
            pass
    try:
        return default_plan(settings)
    except LookupError:
        return None


def allowed_chat_ids_for_plan(settings: Settings, plan_code: str | None) -> set[int]:
    """Chats a subscription on ``plan_code`` may enter.

    A plan with an empty ``chat_ids`` opens every configured protected chat (the explicit
    single-plan convention). A plan that lists chats is restricted to that subset
    (intersected with the configured protected chats). A None/unknown plan falls back to
    the default plan rather than opening everything.
    """
    all_chats = {chat.chat_id for chat in settings.access.protected_chats}
    plan = _resolve_plan_or_default(settings, plan_code)
    if plan is None or not plan.chat_ids:
        return all_chats
    return {chat_id for chat_id in plan.chat_ids if chat_id in all_chats}


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

    async def approve_chat_join_request(self, *, chat_id: int, user_id: int) -> object: ...

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
    chats: list[ProtectedChatConfig] | None = None,
) -> list[AccessInvite]:
    expire_date = now + timedelta(seconds=settings.access.invite_link_ttl_seconds)
    invites: list[AccessInvite] = []
    target_chats = settings.access.protected_chats if chats is None else chats
    for chat in target_chats:
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


async def approve_pending_join_requests(
    bot: SupportsAccessBot,
    settings: Settings,
    *,
    tg_id: int,
    plan_code: str | None = None,
) -> None:
    """Approve any join request the user already has pending in the chats their plan unlocks.

    Addressed directly by ``tg_id`` — the Bot API has no way to *list* pending requests, and
    none is needed here. Best-effort: if the user has no pending request in a chat (or is
    already a member), Telegram raises and we ignore it. This is what admits a user who
    requested to join BEFORE gaining access — e.g. a backlog of pending requests is cleared
    per-user the moment each one pays or is granted, without ever declining anyone.
    """
    for chat_id in allowed_chat_ids_for_plan(settings, plan_code):
        try:
            await bot.approve_chat_join_request(chat_id=chat_id, user_id=tg_id)
        except Exception:
            logger.debug(
                "no pending join request to approve",
                extra={"chat_id": chat_id, "tg_id": tg_id},
            )


async def send_access_links(
    bot: SupportsAccessBot,
    settings: Settings,
    i18n: JsonI18n,
    *,
    tg_id: int,
    lang_code: str,
    now: datetime,
    plan_code: str | None = None,
) -> None:
    allowed = allowed_chat_ids_for_plan(settings, plan_code)

    # Reconcile membership to the current plan: kick the user from any protected chat their
    # plan no longer unlocks (e.g. after switching to a narrower plan). The ban+unban kick
    # is a no-op for a non-member, and for a plan that opens all chats the disallowed set is
    # empty — so this is a no-op for the single-plan setup.
    disallowed = [
        chat.chat_id for chat in settings.access.protected_chats if chat.chat_id not in allowed
    ]
    await _kick_from_chats(bot, settings, tg_id=tg_id, chat_ids=disallowed, now=now)

    # Admit any request the user already had pending in their plan's chats (they may have
    # requested before paying). No-op when there is no pending request.
    await approve_pending_join_requests(bot, settings, tg_id=tg_id, plan_code=plan_code)

    allowed_chats = [
        chat for chat in settings.access.protected_chats if chat.chat_id in allowed
    ]
    if not allowed_chats:
        await bot.send_message(
            chat_id=tg_id,
            text=i18n.gettext(
                "access.empty.text",
                lang_code,
                support_url=_support_url(settings),
            ),
        )
        return

    invites = await create_protected_chat_invite_links(bot, settings, now=now, chats=allowed_chats)
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
    chat_ids = [chat.chat_id for chat in settings.access.protected_chats]
    await _kick_from_chats(bot, settings, tg_id=tg_id, chat_ids=chat_ids, now=now)


async def _kick_from_chats(
    bot: SupportsAccessBot,
    settings: Settings,
    *,
    tg_id: int,
    chat_ids: list[int],
    now: datetime,
) -> None:
    """Temp-ban then unban (a kick that allows a later rejoin) the user from each chat."""
    until_date = now + timedelta(seconds=settings.access.revoke_ban_seconds)
    for chat_id in chat_ids:
        await bot.ban_chat_member(
            chat_id=chat_id,
            user_id=tg_id,
            until_date=until_date,
            revoke_messages=False,
        )
        await bot.unban_chat_member(
            chat_id=chat_id,
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
