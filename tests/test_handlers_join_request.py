from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aiogram import Dispatcher
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.handlers import create_router
from bot.handlers.join_request import handle_chat_join_request
from bot.models import Subscription, User

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
FUTURE = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
PAST = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeSession:
    def __init__(
        self,
        *,
        user: User | None = None,
        subscription: Subscription | None = None,
        raise_on_query: bool = False,
    ) -> None:
        self.user = user
        self.subscription = subscription
        self.raise_on_query = raise_on_query
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def scalar(self, statement: Any) -> Any:
        if self.raise_on_query:
            raise SQLAlchemyError("db unavailable")
        entity = statement.column_descriptions[0]["entity"]
        field = statement.whereclause.left.key
        value = statement.whereclause.right.value
        if entity is User and field == "tg_id":
            return self.user if self.user is not None and self.user.tg_id == value else None
        if entity is Subscription and field == "user_id":
            if self.subscription is not None and self.subscription.user_id == value:
                return self.subscription
            return None
        msg = f"unsupported fake query for {entity}.{field}"
        raise AssertionError(msg)


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


class FakeBot:
    def __init__(self, *, fail: bool = False) -> None:
        self.approvals: list[dict[str, Any]] = []
        self.declines: list[dict[str, Any]] = []
        self.fail = fail

    async def approve_chat_join_request(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("telegram error")
        self.approvals.append(kwargs)

    async def decline_chat_join_request(self, **kwargs: Any) -> None:
        # The handler must NEVER decline any more. Recorded so the `declines == []`
        # assertions below catch a regression that re-introduces a decline.
        self.declines.append(kwargs)


def make_settings() -> Settings:
    return Settings(
        access={
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
    )


def make_event(*, chat_id: int, tg_id: int, username: str | None = None) -> Any:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=tg_id, username=username),
    )


def make_user(*, user_id: int = 1, tg_id: int = 1001) -> User:
    user = User(tg_id=tg_id)
    user.id = user_id
    return user


def make_subscription(
    *,
    user_id: int = 1,
    expires_at: datetime = FUTURE,
    plan_code: str | None = None,
) -> Subscription:
    return Subscription(user_id=user_id, expires_at=expires_at, plan_code=plan_code)


def _plan_restricted_settings() -> Settings:
    # Two protected chats; plan "vip" unlocks only the Research chat (-1002).
    return Settings(
        access={
            "protected_chats": [
                {"chat_id": -1001, "title": "Signals"},
                {"chat_id": -1002, "title": "Research"},
            ],
        },
        tariffs={
            "plans": [{"code": "vip", "chat_ids": [-1002]}],
            "items": [
                {
                    "code": "vip_1m",
                    "plan_code": "vip",
                    "duration_months": 1,
                    "base_price_usd": "59",
                }
            ],
        },
    )


async def test_plan_approves_join_only_for_its_chats() -> None:
    settings = _plan_restricted_settings()
    bot = FakeBot()

    await handle_chat_join_request(
        make_event(chat_id=-1002, tg_id=1001),
        settings,
        FakeSessionFactory(
            FakeSession(
                user=make_user(),
                subscription=make_subscription(expires_at=FUTURE, plan_code="vip"),
            )
        ),
        bot,
        now=NOW,
    )

    assert bot.approvals == [{"chat_id": -1002, "user_id": 1001}]
    assert bot.declines == []


async def test_plan_leaves_pending_for_chat_outside_plan() -> None:
    settings = _plan_restricted_settings()
    bot = FakeBot()

    await handle_chat_join_request(
        make_event(chat_id=-1001, tg_id=1001),
        settings,
        FakeSessionFactory(
            FakeSession(
                user=make_user(),
                subscription=make_subscription(expires_at=FUTURE, plan_code="vip"),
            )
        ),
        bot,
        now=NOW,
    )

    # Plan does not unlock this chat -> leave the request pending, never decline.
    assert bot.approvals == []
    assert bot.declines == []


async def _run(
    session: FakeSession, *, chat_id: int = -1001, tg_id: int = 1001, fail: bool = False
) -> FakeBot:
    bot = FakeBot(fail=fail)
    await handle_chat_join_request(
        make_event(chat_id=chat_id, tg_id=tg_id),
        make_settings(),
        FakeSessionFactory(session),
        bot,
        now=NOW,
    )
    return bot


async def test_approves_join_request_for_active_subscriber() -> None:
    session = FakeSession(user=make_user(), subscription=make_subscription(expires_at=FUTURE))

    bot = await _run(session)

    assert bot.approvals == [{"chat_id": -1001, "user_id": 1001}]
    assert bot.declines == []


async def test_leaves_pending_without_subscription() -> None:
    session = FakeSession(user=make_user(), subscription=None)

    bot = await _run(session)

    assert bot.approvals == []
    assert bot.declines == []


async def test_leaves_pending_with_expired_subscription() -> None:
    session = FakeSession(user=make_user(), subscription=make_subscription(expires_at=PAST))

    bot = await _run(session)

    assert bot.approvals == []
    assert bot.declines == []


async def test_leaves_pending_for_unknown_user() -> None:
    session = FakeSession(user=None, subscription=None)

    bot = await _run(session)

    assert bot.approvals == []
    assert bot.declines == []


async def test_ignores_join_request_for_unmanaged_chat() -> None:
    session = FakeSession(user=make_user(), subscription=make_subscription(expires_at=FUTURE))

    bot = await _run(session, chat_id=-9999)

    assert bot.approvals == []
    assert bot.declines == []
    assert session.committed is False


async def test_leaves_request_pending_on_db_error() -> None:
    session = FakeSession(raise_on_query=True)

    bot = await _run(session)

    assert bot.approvals == []
    assert bot.declines == []


async def test_approve_failure_is_swallowed() -> None:
    session = FakeSession(user=make_user(), subscription=make_subscription(expires_at=FUTURE))

    # approve_chat_join_request raises -> the handler logs and returns without propagating.
    bot = await _run(session, fail=True)

    assert bot.approvals == []
    assert bot.declines == []


def test_router_subscribes_to_chat_join_request_updates() -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())

    # start_polling is called without an explicit allowed_updates, so aiogram derives the
    # delivered update types from the registered handlers; the new handler must be in it or
    # Telegram never sends chat_join_request and the gate silently does nothing.
    assert "chat_join_request" in dispatcher.resolve_used_update_types()
