"""Manually refund a Telegram Stars payment via the Bot API.

Stars has no test network, so the cheap way to exercise the live Stars flow is a
tiny ``test_only`` tariff (see ``config.toml``) paid with real Stars, then refunded
here. The bot deliberately does not expose a refund command (see docs/task.md §10),
so this console tool is the supported path.

Usage:
    # by local invoice id (looks up the buyer + charge id in the database)
    uv run python -m bot.tools.refund_star --invoice-id 123

    # or directly, when you already know the values
    uv run python -m bot.tools.refund_star --user-id 111 --charge-id <telegram_payment_charge_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TextIO

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import load_settings
from bot.core.db import create_engine, create_session_factory, session_scope
from bot.models import Invoice, User


@dataclass(frozen=True, slots=True)
class RefundTarget:
    user_id: int  # Telegram user id (User.tg_id)
    charge_id: str  # telegram_payment_charge_id


class SupportsRefund(Protocol):
    async def refund_star_payment(
        self,
        user_id: int,
        telegram_payment_charge_id: str,
    ) -> bool: ...


async def refund_star(bot: SupportsRefund, target: RefundTarget) -> bool:
    """Call ``refundStarPayment`` for the resolved buyer + charge id."""
    return await bot.refund_star_payment(
        user_id=target.user_id,
        telegram_payment_charge_id=target.charge_id,
    )


async def resolve_target_from_invoice(session: AsyncSession, invoice_id: int) -> RefundTarget:
    """Look up the buyer's Telegram id and the Stars charge id for a local invoice."""
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        msg = f"invoice {invoice_id} was not found"
        raise LookupError(msg)
    if not invoice.telegram_payment_charge_id:
        msg = f"invoice {invoice_id} has no telegram_payment_charge_id (not a paid Stars invoice)"
        raise ValueError(msg)
    user = await session.scalar(select(User).where(User.id == invoice.user_id))
    if user is None:
        msg = f"user {invoice.user_id} for invoice {invoice_id} was not found"
        raise LookupError(msg)
    return RefundTarget(user_id=user.tg_id, charge_id=invoice.telegram_payment_charge_id)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refund a Telegram Stars payment via the Bot API.",
    )
    parser.add_argument(
        "--invoice-id",
        type=int,
        help="Local Invoice.id to refund (resolves buyer + charge id from the database).",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="Telegram user id (use together with --charge-id).",
    )
    parser.add_argument(
        "--charge-id",
        help="telegram_payment_charge_id (use together with --user-id).",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, *, stdout: TextIO) -> int:
    settings = load_settings()
    bot = Bot(token=settings.bot_token.get_secret_value())
    engine = None
    try:
        if args.invoice_id is not None:
            engine = create_engine(settings.database_url)
            session_factory = create_session_factory(engine)
            async with session_scope(session_factory) as session:
                target = await resolve_target_from_invoice(session, args.invoice_id)
        elif args.user_id is not None and args.charge_id:
            target = RefundTarget(user_id=args.user_id, charge_id=args.charge_id)
        else:
            stdout.write("error: pass --invoice-id OR both --user-id and --charge-id\n")
            return 2

        refunded = await refund_star(bot, target)
        stdout.write(
            json.dumps(
                {
                    "refunded": bool(refunded),
                    "user_id": target.user_id,
                    "charge_id": target.charge_id,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 0 if refunded else 1
    finally:
        await bot.session.close()
        if engine is not None:
            await engine.dispose()


def main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args, stdout=stdout))


if __name__ == "__main__":
    raise SystemExit(main())
