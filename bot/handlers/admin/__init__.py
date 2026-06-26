from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command

from bot.filters import IsAdmin
from bot.handlers.admin import broadcast as broadcast_handlers
from bot.handlers.admin import grant as grant_handlers
from bot.handlers.admin import refset as refset_handlers


def create_admin_router() -> Router:
    router = Router(name="admin")
    router.message(Command("grant"), IsAdmin())(grant_handlers.grant)
    router.message(Command("grant"))(grant_handlers.grant_forbidden)
    router.message(Command("refset"), IsAdmin())(refset_handlers.refset)
    router.message(Command("refset"))(refset_handlers.refset_forbidden)
    router.message(Command("broadcast"), IsAdmin())(broadcast_handlers.broadcast)
    router.message(Command("broadcast"))(broadcast_handlers.broadcast_forbidden)
    router.callback_query(F.data.startswith(broadcast_handlers.SEGMENT_CALLBACK_PREFIX), IsAdmin())(
        broadcast_handlers.broadcast_segment_callback
    )
    return router


__all__ = ["create_admin_router"]
