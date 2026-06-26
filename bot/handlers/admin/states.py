from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminGrantState(StatesGroup):
    waiting_input = State()  # "<tg_id> <days>"


class AdminRefsetState(StatesGroup):
    waiting_input = State()  # "<tg_id> <percent>"


class AdminPromoState(StatesGroup):
    code = State()
    kind = State()
    value = State()
    max_uses = State()
    expires_at = State()
    allowed_tariffs = State()
    confirm = State()
