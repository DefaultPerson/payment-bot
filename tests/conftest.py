import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings
from bot.models import Base

# Flat env vars that a deployed .env defines and that would otherwise leak into
# bare ``Settings()`` constructions across the suite.
_AMBIENT_VARS = (
    "BOT_TOKEN",
    "DATABASE_URL",
    "REDIS_URL",
    "ADMIN_IDS",
    "CRYPTOBOT_TOKEN",
    "XROCKET_TOKEN",
    "CRYPTOBOT_TESTNET_TOKEN",
    "XROCKET_TESTNET_TOKEN",
    "SERVICE_CHANNEL_ID",
    "SERVICE_THREAD_ID",
    "LOG_LEVEL",
    "CONFIG_PATH",
)


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``Settings()`` hermetic regardless of the developer's environment.

    The suite constructs bare ``Settings()`` expecting clean defaults. Once the bot is
    configured for deployment a real ``.env`` sits next to the code; without isolation it
    leaks into those tests (and could even surface secrets in assertion diffs). Tests that
    genuinely exercise env/TOML loading pass explicit paths via ``load_settings(...)``,
    whose ``_env_file`` argument overrides the class config and is unaffected by this.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in _AMBIENT_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
async def db_engine():
    """In-memory SQLite engine with the full schema (StaticPool shares one connection).

    Used by repo/aggregation tests that the hand-rolled fakes cannot evaluate (func.sum,
    group_by, EXISTS). Numeric comes back as float in SQLite — assert via Decimal(str(...)).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session
