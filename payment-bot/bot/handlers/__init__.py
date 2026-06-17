from aiogram import Router

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.handlers.start import create_start_router


def create_router(i18n: JsonI18n, settings: Settings) -> Router:
    router = Router(name="handlers")
    router.include_router(create_start_router(i18n, settings))
    return router
