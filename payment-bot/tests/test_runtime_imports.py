import importlib

import pytest
from aiohttp import web

from bot import __main__ as main_module
from bot.config import Settings
from bot.jobs.runner import TaskRunner
from bot.payments.cryptobot import CryptoBotClient


def test_main_module_imports_without_runtime_side_effects() -> None:
    module = importlib.import_module("bot.__main__")

    assert hasattr(module, "main")
    assert hasattr(module, "run")


def test_build_cryptobot_client_respects_enabled_flag_and_token() -> None:
    assert main_module.build_cryptobot_client(Settings()) is None
    assert (
        main_module.build_cryptobot_client(
            Settings(cryptobot={"enabled": False, "token": "secret"}),
        )
        is None
    )

    client = main_module.build_cryptobot_client(
        Settings(cryptobot={"token": "secret", "api_url": "https://testnet-pay.crypt.bot"}),
    )

    assert isinstance(client, CryptoBotClient)
    assert client.token == "secret"
    assert client.api_url == "https://testnet-pay.crypt.bot"


def test_start_background_jobs_registers_notifications_and_optional_crypto(monkeypatch) -> None:
    started: list[tuple[str, object]] = []

    def fake_start(self: TaskRunner, name: str, coroutine: object) -> None:
        started.append((name, coroutine))
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()

    async def fake_notification_loop(**kwargs: object) -> None:
        return None

    async def fake_crypto_loop(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(TaskRunner, "start", fake_start)
    monkeypatch.setattr(main_module, "notification_loop", fake_notification_loop, raising=False)
    monkeypatch.setattr(main_module, "crypto_poller_loop", fake_crypto_loop)

    main_module.start_background_jobs(
        task_runner=TaskRunner(),
        session_factory=object(),
        settings=Settings(),
        bot=object(),
        i18n=object(),
        service_channel=object(),
        cryptobot_client=None,
    )

    assert [name for name, _coroutine in started] == ["notifications"]

    started.clear()
    main_module.start_background_jobs(
        task_runner=TaskRunner(),
        session_factory=object(),
        settings=Settings(),
        bot=object(),
        i18n=object(),
        service_channel=object(),
        cryptobot_client=object(),
    )

    assert [name for name, _coroutine in started] == ["notifications", "crypto-poller"]


async def test_bot_identity_cache_throttles_failed_checks() -> None:
    class FailingBot:
        def __init__(self) -> None:
            self.calls = 0

        async def get_me(self) -> None:
            self.calls += 1
            raise RuntimeError("telegram unavailable")

    bot = FailingBot()
    cache = main_module.BotIdentityCache(bot, ttl_seconds=30)

    assert await cache.check() is False
    assert await cache.check() is False
    assert bot.calls == 1


async def test_start_http_server_cleans_runner_when_site_start_fails(monkeypatch) -> None:
    cleanup_called = False

    class FakeRunner:
        def __init__(self, app: web.Application) -> None:
            self.app = app

        async def setup(self) -> None:
            return None

        async def cleanup(self) -> None:
            nonlocal cleanup_called
            cleanup_called = True

    class FakeSite:
        def __init__(self, runner: FakeRunner, *, host: str, port: int) -> None:
            self.runner = runner
            self.host = host
            self.port = port

        async def start(self) -> None:
            raise OSError("port unavailable")

    monkeypatch.setattr(main_module.web, "AppRunner", FakeRunner)
    monkeypatch.setattr(main_module.web, "TCPSite", FakeSite)

    with pytest.raises(OSError, match="port unavailable"):
        await main_module.start_http_server(web.Application(), host="127.0.0.1", port=1)

    assert cleanup_called is True
