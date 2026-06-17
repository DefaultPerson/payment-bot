import importlib

import pytest
from aiohttp import web

from bot import __main__ as main_module


def test_main_module_imports_without_runtime_side_effects() -> None:
    module = importlib.import_module("bot.__main__")

    assert hasattr(module, "main")
    assert hasattr(module, "run")


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
