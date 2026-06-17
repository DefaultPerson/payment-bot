from sqlalchemy.ext.asyncio import AsyncEngine

from bot.core.db import create_engine, create_session_factory, mask_database_url


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
