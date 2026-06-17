from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.config import Settings


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message, settings: Settings) -> bool:
        user = event.from_user
        return bool(user and user.id in settings.admin_ids)
