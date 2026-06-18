import importlib

import pytest
from aiohttp import web

from bot import __main__ as main_module
from bot.config import Settings
from bot.jobs.runner import TaskRunner
from bot.payments.cryptobot import CryptoBotClient
from bot.payments.xrocket import XRocketClient


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


def test_build_xrocket_client_respects_enabled_flag_and_token() -> None:
    assert main_module.build_xrocket_client(Settings()) is None
    assert (
        main_module.build_xrocket_client(
            Settings(xrocket={"enabled": False, "token": "secret"}),
        )
        is None
    )

    client = main_module.build_xrocket_client(
        Settings(
            xrocket={
                "enabled": True,
                "token": "secret",
                "testnet": True,
                "base_url": "https://pay.xrocket.test",
            },
        ),
    )

    assert isinstance(client, XRocketClient)
    assert client.api_key == "secret"
    assert client.testnet is True
    assert client.base_url == "https://pay.xrocket.test"


def test_start_background_jobs_registers_notifications_and_optional_payment_pollers(
    monkeypatch,
) -> None:
    started: list[tuple[str, object]] = []
    poller_kwargs: list[dict[str, object]] = []
    maintenance_kwargs: list[dict[str, object]] = []

    def fake_start(self: TaskRunner, name: str, coroutine: object) -> None:
        started.append((name, coroutine))
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()

    async def fake_notification_loop(**kwargs: object) -> None:
        return None

    def fake_crypto_loop(**kwargs: object) -> object:
        poller_kwargs.append({"name": "crypto", **kwargs})

        async def noop() -> None:
            return None

        return noop()

    def fake_xrocket_loop(**kwargs: object) -> object:
        poller_kwargs.append({"name": "xrocket", **kwargs})

        async def noop() -> None:
            return None

        return noop()

    def fake_invoice_cleanup_loop(**kwargs: object) -> object:
        maintenance_kwargs.append({"name": "invoice-cleanup", **kwargs})

        async def noop() -> None:
            return None

        return noop()

    def fake_metrics_snapshot_loop(**kwargs: object) -> object:
        maintenance_kwargs.append({"name": "metrics-snapshot", **kwargs})

        async def noop() -> None:
            return None

        return noop()

    monkeypatch.setattr(TaskRunner, "start", fake_start)
    monkeypatch.setattr(main_module, "notification_loop", fake_notification_loop, raising=False)
    monkeypatch.setattr(main_module, "crypto_poller_loop", fake_crypto_loop)
    monkeypatch.setattr(main_module, "xrocket_poller_loop", fake_xrocket_loop, raising=False)
    monkeypatch.setattr(
        main_module,
        "invoice_cleanup_loop",
        fake_invoice_cleanup_loop,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "metrics_snapshot_loop",
        fake_metrics_snapshot_loop,
        raising=False,
    )

    main_module.start_background_jobs(
        task_runner=TaskRunner(),
        session_factory=object(),
        settings=Settings(),
        bot=object(),
        i18n=object(),
        service_channel=object(),
        cryptobot_client=None,
        xrocket_client=None,
        metrics=object(),
    )

    assert [name for name, _coroutine in started] == [
        "notifications",
        "invoice-cleanup",
        "metrics-snapshot",
    ]

    started.clear()
    maintenance_kwargs.clear()
    main_module.start_background_jobs(
        task_runner=TaskRunner(),
        session_factory=object(),
        settings=Settings(),
        bot=object(),
        i18n=object(),
        service_channel=object(),
        cryptobot_client=object(),
        xrocket_client=None,
        metrics=object(),
    )

    assert [name for name, _coroutine in started] == [
        "notifications",
        "invoice-cleanup",
        "metrics-snapshot",
        "crypto-poller",
    ]

    started.clear()
    maintenance_kwargs.clear()
    metrics = object()
    session_factory = object()
    settings = Settings()
    main_module.start_background_jobs(
        task_runner=TaskRunner(),
        session_factory=session_factory,
        settings=settings,
        bot=object(),
        i18n=object(),
        service_channel=object(),
        cryptobot_client=object(),
        xrocket_client=object(),
        metrics=metrics,
    )

    assert [name for name, _coroutine in started] == [
        "notifications",
        "invoice-cleanup",
        "metrics-snapshot",
        "crypto-poller",
        "xrocket-poller",
    ]
    assert [kwargs["metrics"] for kwargs in poller_kwargs[-2:]] == [metrics, metrics]
    assert maintenance_kwargs[-2:] == [
        {
            "name": "invoice-cleanup",
            "session_factory": session_factory,
            "settings": settings,
        },
        {
            "name": "metrics-snapshot",
            "session_factory": session_factory,
            "settings": settings,
            "metrics": metrics,
        },
    ]


async def test_close_payment_clients_closes_configured_clients() -> None:
    class CloseableClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    cryptobot_client = CloseableClient()
    xrocket_client = CloseableClient()

    await main_module.close_payment_clients(
        cryptobot_client=cryptobot_client,
        xrocket_client=xrocket_client,
    )

    assert cryptobot_client.closed is True
    assert xrocket_client.closed is True

    await main_module.close_payment_clients(cryptobot_client=None, xrocket_client=None)


async def test_close_payment_clients_continues_after_individual_close_error(
    caplog,
) -> None:
    class FailingClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True
            raise RuntimeError("close failed")

    class CloseableClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    cryptobot_client = FailingClient()
    xrocket_client = CloseableClient()

    await main_module.close_payment_clients(
        cryptobot_client=cryptobot_client,
        xrocket_client=xrocket_client,
    )

    assert cryptobot_client.closed is True
    assert xrocket_client.closed is True
    assert "failed to close payment client" in caplog.text


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
