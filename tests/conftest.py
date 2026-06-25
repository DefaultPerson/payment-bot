import pytest

from bot.config import Settings

# Flat env vars that a deployed .env defines and that would otherwise leak into
# bare ``Settings()`` constructions across the suite.
_AMBIENT_VARS = (
    "BOT_TOKEN",
    "DATABASE_URL",
    "REDIS_URL",
    "ADMIN_IDS",
    "CRYPTOBOT_TOKEN",
    "XROCKET_TOKEN",
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
