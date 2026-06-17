from aiogram import Router

from bot.handlers.start import create_start_router


def create_router() -> Router:
    router = Router(name="handlers")
    router.include_router(create_start_router())
    return router
