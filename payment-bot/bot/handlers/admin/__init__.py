from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command

from bot.filters import IsAdmin
from bot.handlers.admin import grant as grant_handlers


def create_admin_router() -> Router:
    router = Router(name="admin")
    router.message(Command("grant"), IsAdmin())(grant_handlers.grant)
    router.message(Command("grant"))(grant_handlers.grant_forbidden)
    return router


__all__ = ["create_admin_router"]
