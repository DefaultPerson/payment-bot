from __future__ import annotations

from aiogram import F, Router

from bot.filters import IsAdmin
from bot.handlers.admin import broadcast as broadcast_handlers
from bot.handlers.admin import grant as grant_handlers
from bot.handlers.admin import panel as panel_handlers
from bot.handlers.admin import promo as promo_handlers
from bot.handlers.admin import refreset as refreset_handlers
from bot.handlers.admin import refset as refset_handlers
from bot.handlers.admin import stats as stats_handlers
from bot.handlers.admin.states import (
    AdminGrantState,
    AdminPromoState,
    AdminRefResetState,
    AdminRefsetState,
)
from bot.keyboards.admin import (
    CB_ADM_BCAST,
    CB_ADM_CANCEL,
    CB_ADM_CLOSE,
    CB_ADM_GRANT,
    CB_ADM_NOOP,
    CB_ADM_PROMO,
    CB_ADM_PROMO_CONFIRM,
    CB_ADM_PROMO_NEW,
    CB_ADM_PROMO_TARIFF_DONE,
    CB_ADM_REFRESET,
    CB_ADM_REFSET,
    CB_ADM_ROOT,
    CB_ADM_SKIP,
    CB_ADM_STATS,
    PFX_ADM_PROMO_DEACT,
    PFX_ADM_PROMO_KIND,
    PFX_ADM_PROMO_PAGE,
    PFX_ADM_PROMO_TARIFF,
    PFX_ADM_STATS_PERIOD,
)
from bot.keyboards.menu import CB_ADMIN


def create_admin_router() -> Router:
    router = Router(name="admin")

    # Panel root / navigation.
    router.callback_query(F.data == CB_ADMIN, IsAdmin())(panel_handlers.open_panel)
    router.callback_query(F.data == CB_ADM_ROOT, IsAdmin())(panel_handlers.open_panel)
    router.callback_query(F.data == CB_ADM_CANCEL, IsAdmin())(panel_handlers.open_panel)
    router.callback_query(F.data == CB_ADM_CLOSE, IsAdmin())(panel_handlers.close_panel)
    router.callback_query(F.data == CB_ADM_NOOP, IsAdmin())(panel_handlers.noop)

    # Grant (FSM).
    router.callback_query(F.data == CB_ADM_GRANT, IsAdmin())(grant_handlers.grant_start)
    router.message(AdminGrantState.waiting_input, IsAdmin())(grant_handlers.grant_input)

    # Refset (FSM).
    router.callback_query(F.data == CB_ADM_REFSET, IsAdmin())(refset_handlers.refset_start)
    router.message(AdminRefsetState.waiting_input, IsAdmin())(refset_handlers.refset_input)

    # Refreset — zero a user's referral balance (FSM).
    router.callback_query(F.data == CB_ADM_REFRESET, IsAdmin())(refreset_handlers.refreset_start)
    router.message(AdminRefResetState.waiting_input, IsAdmin())(refreset_handlers.refreset_input)

    # Broadcast (panel entry + the existing segment composer).
    router.callback_query(F.data == CB_ADM_BCAST, IsAdmin())(broadcast_handlers.broadcast_entry)
    router.callback_query(
        F.data.startswith(broadcast_handlers.SEGMENT_CALLBACK_PREFIX),
        IsAdmin(),
    )(broadcast_handlers.broadcast_segment_callback)

    # Stats.
    router.callback_query(F.data == CB_ADM_STATS, IsAdmin())(stats_handlers.open_stats)
    router.callback_query(F.data.startswith(PFX_ADM_STATS_PERIOD), IsAdmin())(
        stats_handlers.stats_period
    )

    # Promo — list / pagination / deactivate.
    router.callback_query(F.data == CB_ADM_PROMO, IsAdmin())(promo_handlers.promo_list)
    router.callback_query(F.data.startswith(PFX_ADM_PROMO_PAGE), IsAdmin())(
        promo_handlers.promo_page
    )
    router.callback_query(F.data.startswith(PFX_ADM_PROMO_DEACT), IsAdmin())(
        promo_handlers.promo_deactivate
    )

    # Promo — create FSM.
    router.callback_query(F.data == CB_ADM_PROMO_NEW, IsAdmin())(promo_handlers.promo_new_start)
    router.message(AdminPromoState.code, IsAdmin())(promo_handlers.promo_code_input)
    router.callback_query(
        F.data.startswith(PFX_ADM_PROMO_KIND),
        AdminPromoState.kind,
        IsAdmin(),
    )(promo_handlers.promo_kind_choice)
    router.message(AdminPromoState.value, IsAdmin())(promo_handlers.promo_value_input)
    router.callback_query(F.data == CB_ADM_SKIP, AdminPromoState.max_uses, IsAdmin())(
        promo_handlers.promo_maxuses_skip
    )
    router.message(AdminPromoState.max_uses, IsAdmin())(promo_handlers.promo_maxuses_input)
    router.callback_query(F.data == CB_ADM_SKIP, AdminPromoState.expires_at, IsAdmin())(
        promo_handlers.promo_expires_skip
    )
    router.message(AdminPromoState.expires_at, IsAdmin())(promo_handlers.promo_expires_input)
    # Registered before the tariff-toggle startswith handler so "...:done" is not shadowed.
    router.callback_query(
        F.data == CB_ADM_PROMO_TARIFF_DONE,
        AdminPromoState.allowed_tariffs,
        IsAdmin(),
    )(promo_handlers.promo_tariffs_done)
    router.callback_query(
        F.data.startswith(PFX_ADM_PROMO_TARIFF),
        AdminPromoState.allowed_tariffs,
        IsAdmin(),
    )(promo_handlers.promo_tariff_toggle)
    router.callback_query(
        F.data == CB_ADM_PROMO_CONFIRM,
        AdminPromoState.confirm,
        IsAdmin(),
    )(promo_handlers.promo_confirm)

    return router


__all__ = ["create_admin_router"]
