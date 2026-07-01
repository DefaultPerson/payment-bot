from aiogram import Router

from bot.handlers.admin import create_admin_router
from bot.handlers.buy import create_buy_router
from bot.handlers.join_request import create_join_request_router
from bot.handlers.language import create_language_router
from bot.handlers.profile import create_profile_router
from bot.handlers.promo import create_promo_router
from bot.handlers.referral import create_referral_router
from bot.handlers.service_cleanup import create_service_cleanup_router
from bot.handlers.stars import create_stars_router
from bot.handlers.start import create_start_router
from bot.handlers.support import create_support_router
from bot.handlers.trial import create_trial_router


def create_router() -> Router:
    router = Router(name="handlers")
    router.include_router(create_start_router())
    router.include_router(create_admin_router())
    router.include_router(create_profile_router())
    router.include_router(create_language_router())
    router.include_router(create_buy_router())
    router.include_router(create_trial_router())
    router.include_router(create_promo_router())
    router.include_router(create_referral_router())
    router.include_router(create_stars_router())
    router.include_router(create_support_router())
    router.include_router(create_join_request_router())
    router.include_router(create_service_cleanup_router())
    return router
