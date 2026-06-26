import pytest

from bot.models import Invoice, User
from bot.tools.refund_star import (
    RefundTarget,
    refund_star,
    resolve_target_from_invoice,
)


class _FakeBot:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    async def refund_star_payment(
        self,
        *,
        user_id: int,
        telegram_payment_charge_id: str,
    ) -> bool:
        self.calls.append((user_id, telegram_payment_charge_id))
        return self.result


class _FakeSession:
    def __init__(self, results: list[object]) -> None:
        self._results = list(results)

    async def scalar(self, _statement: object) -> object:
        return self._results.pop(0)


async def test_refund_star_invokes_bot_api() -> None:
    bot = _FakeBot(result=True)

    refunded = await refund_star(bot, RefundTarget(user_id=111, charge_id="abc"))

    assert refunded is True
    assert bot.calls == [(111, "abc")]


async def test_refund_star_propagates_negative_result() -> None:
    bot = _FakeBot(result=False)

    refunded = await refund_star(bot, RefundTarget(user_id=1, charge_id="x"))

    assert refunded is False


async def test_resolve_target_from_invoice_returns_tg_id_and_charge() -> None:
    invoice = Invoice(id=5, user_id=1, telegram_payment_charge_id="charge-xyz")
    user = User(id=1, tg_id=111)
    session = _FakeSession([invoice, user])

    target = await resolve_target_from_invoice(session, 5)

    assert target == RefundTarget(user_id=111, charge_id="charge-xyz")


async def test_resolve_target_rejects_invoice_without_charge_id() -> None:
    invoice = Invoice(id=6, user_id=1, telegram_payment_charge_id=None)
    session = _FakeSession([invoice])

    with pytest.raises(ValueError, match="telegram_payment_charge_id"):
        await resolve_target_from_invoice(session, 6)


async def test_resolve_target_missing_invoice_raises() -> None:
    session = _FakeSession([None])

    with pytest.raises(LookupError, match="was not found"):
        await resolve_target_from_invoice(session, 999)
