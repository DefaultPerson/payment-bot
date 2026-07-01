from __future__ import annotations

from dataclasses import dataclass

from aiogram import Dispatcher
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage

from bot.config import Settings
from bot.handlers.service_cleanup import (
    create_service_cleanup_router,
    delete_service_message,
)

MANAGED_CHAT = -1001
UNMANAGED_CHAT = -9999


@dataclass(slots=True)
class FakeChat:
    id: int


class FakeMessage:
    def __init__(self, *, chat: FakeChat, content_type: str, message_id: int = 42) -> None:
        self.chat = chat
        self.content_type = content_type
        self.message_id = message_id
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeBadRequestMessage(FakeMessage):
    async def delete(self) -> None:
        raise TelegramBadRequest(
            method=DeleteMessage(chat_id=self.chat.id, message_id=self.message_id),
            message="message to delete not found",
        )


def make_settings() -> Settings:
    return Settings(
        access={"protected_chats": [{"chat_id": MANAGED_CHAT, "title": "Signals"}]},
    )


def make_message(
    *,
    chat_id: int,
    content_type: str = ContentType.NEW_CHAT_MEMBERS,
    cls: type[FakeMessage] = FakeMessage,
) -> FakeMessage:
    return cls(chat=FakeChat(id=chat_id), content_type=content_type)


async def test_deletes_join_message_in_managed_chat() -> None:
    message = make_message(chat_id=MANAGED_CHAT, content_type=ContentType.NEW_CHAT_MEMBERS)

    await delete_service_message(message, make_settings())

    assert message.deleted is True


async def test_deletes_leave_message_in_managed_chat() -> None:
    message = make_message(chat_id=MANAGED_CHAT, content_type=ContentType.LEFT_CHAT_MEMBER)

    await delete_service_message(message, make_settings())

    assert message.deleted is True


async def test_does_not_delete_in_unmanaged_chat() -> None:
    message = make_message(chat_id=UNMANAGED_CHAT)

    await delete_service_message(message, make_settings())

    assert message.deleted is False


async def test_swallows_bad_request_when_delete_fails() -> None:
    message = make_message(chat_id=MANAGED_CHAT, cls=FakeBadRequestMessage)

    # Must not raise even though delete() fails (message too old / already gone / no rights).
    await delete_service_message(message, make_settings())


def test_router_subscribes_to_message_updates() -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_service_cleanup_router())

    # start_polling runs without an explicit allowed_updates, so aiogram derives delivered
    # update types from registered handlers; "message" must be present or Telegram never
    # sends the service messages this handler deletes.
    assert "message" in dispatcher.resolve_used_update_types()
