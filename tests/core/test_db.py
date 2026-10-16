from sqlalchemy.ext.asyncio import AsyncEngine

from bot.core.db import (
    check_database,
    create_engine,
    create_session_factory,
    mask_database_url,
    session_scope,
)


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session = FakeSession()

    def __call__(self) -> FakeSession:
        return self.session


class FakeConnection:
    def __init__(self) -> None:
        self.executed_statement = None

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def execute(self, statement) -> None:
        self.executed_statement = statement


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def connect(self) -> FakeConnection:
        return self.connection


def test_create_engine_returns_async_engine() -> None:
    engine = create_engine("postgresql+asyncpg://user:pass@localhost:5432/payment_bot")

    assert isinstance(engine, AsyncEngine)


def test_create_session_factory_disables_expiration() -> None:
    engine = create_engine("postgresql+asyncpg://user:pass@localhost:5432/payment_bot")
    session_factory = create_session_factory(engine)

    assert session_factory.kw["expire_on_commit"] is False


def test_mask_database_url_redacts_password() -> None:
    masked = mask_database_url("postgresql+asyncpg://user:secret@localhost:5432/payment_bot")

    assert "secret" not in masked
    assert "***" in masked


async def test_session_scope_commits_on_success() -> None:
    session_factory = FakeSessionFactory()

    async with session_scope(session_factory):
        pass

    assert session_factory.session.committed is True
    assert session_factory.session.rolled_back is False


async def test_session_scope_rolls_back_on_exception() -> None:
    session_factory = FakeSessionFactory()

    try:
        async with session_scope(session_factory):
            msg = "force rollback"
            raise RuntimeError(msg)
    except RuntimeError:
        pass

    assert session_factory.session.committed is False
    assert session_factory.session.rolled_back is True


async def test_check_database_executes_select_one() -> None:
    engine = FakeEngine()

    result = await check_database(engine)

    assert result is True
    assert str(engine.connection.executed_statement) == "select 1"
