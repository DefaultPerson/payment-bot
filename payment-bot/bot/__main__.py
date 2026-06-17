from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from bot.config import Settings, load_settings
from bot.core.db import check_database, create_engine, create_session_factory
from bot.core.http import HealthDependencies, HealthState, create_http_app
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel, setup_json_logging
from bot.core.metrics import create_metrics_registry
from bot.core.redis import check_redis, create_redis_client
from bot.handlers import create_router
from bot.jobs.crypto_poller import crypto_poller_loop
from bot.jobs.runner import TaskRunner
from bot.payments.cryptobot import CryptoBotClient

logger = logging.getLogger(__name__)


class BotIdentityCache:
    def __init__(self, bot: Bot, *, ttl_seconds: int) -> None:
        self.bot = bot
        self.ttl_seconds = ttl_seconds
        self._last_checked = 0.0
        self._last_ok = False

    async def check(self) -> bool:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now - self._last_checked < self.ttl_seconds:
            return self._last_ok

        self._last_checked = now
        try:
            await self.bot.get_me()
        except Exception:
            self._last_ok = False
            return False

        self._last_ok = True
        return True


async def start_http_server(app: web.Application, *, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    return runner


def install_signal_handlers(stop: Callable[[], None]) -> None:
    loop = asyncio.get_running_loop()

    def fallback_handler(_signum: int, _frame: FrameType | None) -> None:
        stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, fallback_handler)


async def stop_dispatcher(dispatcher: Dispatcher) -> None:
    try:
        await dispatcher.stop_polling()
    except RuntimeError:
        pass


def build_cryptobot_client(settings: Settings) -> CryptoBotClient | None:
    if not settings.cryptobot.enabled or settings.cryptobot.token is None:
        return None
    return CryptoBotClient(
        token=settings.cryptobot.token.get_secret_value(),
        api_url=settings.cryptobot.api_url,
    )


async def main() -> None:
    settings = load_settings()
    settings.validate_runtime()
    setup_json_logging(settings)

    health_state = HealthState()
    task_runner = TaskRunner()
    engine = None
    redis_client = None
    bot = None
    dispatcher = None
    cryptobot_client = None
    http_runner = None

    try:
        engine = create_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        redis_client = create_redis_client(settings.redis_url)
        bot = Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dispatcher = Dispatcher()
        i18n = JsonI18n(
            locales_dir=Path(__file__).resolve().parent / "locales",
            fallback_language=settings.bot.fallback_language,
        )
        service_channel = ServiceChannel(bot, settings)
        cryptobot_client = build_cryptobot_client(settings)
        dispatcher["settings"] = settings
        dispatcher["i18n"] = i18n
        dispatcher["session_factory"] = session_factory
        dispatcher["service_channel"] = service_channel
        dispatcher["cryptobot_client"] = cryptobot_client
        dispatcher.include_router(create_router())
        if cryptobot_client is not None:
            task_runner.start(
                "crypto-poller",
                crypto_poller_loop(
                    session_factory=session_factory,
                    settings=settings,
                    client=cryptobot_client,
                    bot=bot,
                    i18n=i18n,
                    service_channel=service_channel,
                ),
            )

        bot_identity = BotIdentityCache(bot, ttl_seconds=settings.http.bot_cache_seconds)
        http_app = create_http_app(
            HealthDependencies(
                check_db=lambda: check_database(engine),
                check_redis=lambda: check_redis(redis_client),
                check_bot=bot_identity.check,
            ),
            health_state,
            registry=create_metrics_registry(),
        )
        http_runner = await start_http_server(
            http_app,
            host=settings.http.host,
            port=settings.http.port,
        )
        stop_event = asyncio.Event()
        install_signal_handlers(stop_event.set)

        polling_task = asyncio.create_task(dispatcher.start_polling(bot), name="aiogram-polling")
        stop_task = asyncio.create_task(stop_event.wait(), name="shutdown-wait")
        done, pending = await asyncio.wait(
            {polling_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            health_state.shutting_down = True
            await stop_dispatcher(dispatcher)

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if polling_task in done:
            polling_task.result()
    finally:
        health_state.shutting_down = True
        if dispatcher is not None:
            await stop_dispatcher(dispatcher)
        await task_runner.stop()
        if cryptobot_client is not None:
            await cryptobot_client.close()
        if http_runner is not None:
            await http_runner.cleanup()
        if bot is not None:
            await bot.session.close()
        if redis_client is not None:
            await redis_client.aclose()
        if engine is not None:
            await engine.dispose()
        logger.info("shutdown complete")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
