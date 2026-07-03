from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from types import FrameType

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_broadcast import BroadcastScheduler, BroadcastService, RedisBroadcastStorage
from aiogram_broadcast.ui import BroadcastUIHandlers
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings, load_settings
from bot.core.db import check_database, create_engine, create_session_factory
from bot.core.http import HealthDependencies, HealthState, create_http_app
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel, setup_json_logging
from bot.core.menu import configure_commands
from bot.core.metrics import PaymentBotMetrics, create_metrics
from bot.core.redis import check_redis, create_redis_client
from bot.core.service_events import (
    apply_valid_event_emoji,
    configured_event_emoji_ids,
    disable_event_emoji,
)
from bot.core.text_emoji import (
    apply_valid_text_emoji,
    configured_text_emoji_ids,
    disable_text_emoji,
)
from bot.handlers import create_router
from bot.handlers.provider_webhooks import (
    ProviderWebhookDeps,
    make_cryptobot_webhook_handler,
    make_xrocket_webhook_handler,
)
from bot.handlers.tribute_webhook import TributeWebhookDeps, create_tribute_webhook_handler
from bot.jobs.crypto_poller import crypto_poller_loop
from bot.jobs.maintenance import invoice_cleanup_loop, metrics_snapshot_loop
from bot.jobs.notifications import notification_loop
from bot.jobs.runner import TaskRunner
from bot.jobs.xrocket_poller import xrocket_poller_loop
from bot.keyboards.icons import apply_valid_icon_ids, configured_icon_ids, disable_icons
from bot.middlewares import ActivityMiddleware, FsmCommandExitMiddleware
from bot.payments.cryptobot import CryptoBotClient
from bot.payments.xrocket import XRocketClient
from bot.services.broadcast_ui import (
    PersistentBroadcastUIMiddleware,
    create_redis_jobstore,
    set_scheduled_broadcast_bot,
)

# FSM states whose flows a slash-command must be able to escape (see FsmCommandExitMiddleware).
_FSM_EXIT_PREFIXES = (
    "BroadcastUIState",
    "AdminGrantState",
    "AdminRefsetState",
    "AdminPromoState",
)

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


async def resolve_bot_username(bot: Bot, settings: Settings) -> None:
    """Auto-derive @username from Telegram unless config provides an override."""
    if settings.bot.username_override:
        settings.bot.username = settings.bot.username_override
        return
    try:
        me = await bot.get_me()
    except Exception:
        logger.warning("get_me failed; keeping configured bot username", exc_info=True)
        return
    if me.username:
        settings.bot.username = me.username


async def configure_menu_icons(bot: Bot, settings: Settings) -> None:
    """Validate custom-emoji button icons; keep only ids Telegram confirms valid.

    A stale ``custom_emoji_id`` raises ``CUSTOM_EMOJI_INVALID`` and would fail the whole
    message send, so an unverifiable id is dropped (button renders without an icon).
    """
    if not settings.bot.custom_emoji_icons:
        disable_icons()
        return
    ids = configured_icon_ids()
    if not ids:
        return
    try:
        stickers = await bot.get_custom_emoji_stickers(ids)
    except Exception:
        logger.warning("menu icon validation failed; disabling button icons", exc_info=True)
        disable_icons()
        return
    valid = {sticker.custom_emoji_id for sticker in stickers}
    apply_valid_icon_ids(valid)
    dropped = sorted(set(ids) - valid)
    if dropped:
        logger.warning("dropped invalid menu icon ids", extra={"icon_ids": dropped})


async def configure_event_emoji(bot: Bot, settings: Settings) -> None:
    """Validate custom-emoji glyphs for service-channel events; fall back to plain unicode.

    A stale ``custom_emoji_id`` raises ``CUSTOM_EMOJI_INVALID`` and would fail the whole
    notification send, so any id Telegram cannot confirm is dropped to its plain glyph.
    """
    if not settings.bot.custom_emoji_icons:
        disable_event_emoji()
        return
    ids = configured_event_emoji_ids()
    if not ids:
        return
    try:
        stickers = await bot.get_custom_emoji_stickers(ids)
    except Exception:
        logger.warning("event emoji validation failed; using plain emoji", exc_info=True)
        disable_event_emoji()
        return
    valid = {sticker.custom_emoji_id for sticker in stickers}
    apply_valid_event_emoji(valid)
    dropped = sorted(set(ids) - valid)
    if dropped:
        logger.warning("dropped invalid event emoji ids", extra={"emoji_ids": dropped})


async def configure_text_emoji(bot: Bot, settings: Settings) -> None:
    """Validate custom-emoji glyphs used inside message text; fall back to plain unicode.

    A stale ``custom_emoji_id`` raises ``CUSTOM_EMOJI_INVALID`` and would fail the whole
    message send, so any id Telegram cannot confirm is dropped to its plain glyph.
    """
    if not settings.bot.custom_emoji_icons:
        disable_text_emoji()
        return
    ids = configured_text_emoji_ids()
    if not ids:
        return
    try:
        stickers = await bot.get_custom_emoji_stickers(ids)
    except Exception:
        logger.warning("text emoji validation failed; using plain emoji", exc_info=True)
        disable_text_emoji()
        return
    valid = {sticker.custom_emoji_id for sticker in stickers}
    apply_valid_text_emoji(valid)
    dropped = sorted(set(ids) - valid)
    if dropped:
        logger.warning("dropped invalid text emoji ids", extra={"emoji_ids": dropped})


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


def build_xrocket_client(settings: Settings) -> XRocketClient | None:
    if not settings.xrocket.enabled or settings.xrocket.token is None:
        return None
    return XRocketClient(
        api_key=settings.xrocket.token.get_secret_value(),
        testnet=settings.xrocket.testnet,
        base_url=settings.xrocket.base_url,
    )


def _build_payment_webhooks(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    metrics: PaymentBotMetrics,
    cryptobot_client: CryptoBotClient | None,
    xrocket_client: XRocketClient | None,
) -> list[tuple[str, Callable[..., object]]]:
    """Inbound webhook routes for enabled providers. Pollers stay as a fallback."""
    webhooks: list[tuple[str, Callable[..., object]]] = []
    if settings.tribute.enabled and settings.tribute.token is not None:
        webhooks.append(
            (
                settings.tribute.webhook_path,
                create_tribute_webhook_handler(
                    TributeWebhookDeps(
                        session_factory=session_factory,
                        settings=settings,
                        bot=bot,
                        i18n=i18n,
                        service_channel=service_channel,
                        metrics=metrics,
                    )
                ),
            )
        )
    provider_deps = ProviderWebhookDeps(
        session_factory=session_factory,
        settings=settings,
        bot=bot,
        i18n=i18n,
        service_channel=service_channel,
        metrics=metrics,
    )
    if cryptobot_client is not None:
        webhooks.append(
            (settings.cryptobot.webhook_path, make_cryptobot_webhook_handler(provider_deps))
        )
    if xrocket_client is not None:
        webhooks.append(
            (settings.xrocket.webhook_path, make_xrocket_webhook_handler(provider_deps))
        )
    return webhooks


async def close_payment_clients(
    *,
    cryptobot_client: CryptoBotClient | None,
    xrocket_client: XRocketClient | None,
) -> None:
    await _close_payment_client("cryptobot", cryptobot_client)
    await _close_payment_client("xrocket", xrocket_client)


async def _close_payment_client(
    provider: str,
    client: CryptoBotClient | XRocketClient | None,
) -> None:
    if client is None:
        return
    try:
        await client.close()
    except Exception:
        logger.exception(
            "failed to close payment client",
            extra={"provider": provider},
        )


def start_background_jobs(
    *,
    task_runner: TaskRunner,
    session_factory,
    settings: Settings,
    bot: Bot,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    cryptobot_client: CryptoBotClient | None,
    xrocket_client: XRocketClient | None,
    metrics: PaymentBotMetrics,
) -> None:
    task_runner.start(
        "notifications",
        notification_loop(
            session_factory=session_factory,
            settings=settings,
            bot=bot,
            i18n=i18n,
            service_channel=service_channel,
        ),
    )
    task_runner.start(
        "invoice-cleanup",
        invoice_cleanup_loop(
            session_factory=session_factory,
            settings=settings,
        ),
    )
    task_runner.start(
        "metrics-snapshot",
        metrics_snapshot_loop(
            session_factory=session_factory,
            settings=settings,
            metrics=metrics,
        ),
    )
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
                metrics=metrics,
            ),
        )
    if xrocket_client is not None:
        task_runner.start(
            "xrocket-poller",
            xrocket_poller_loop(
                session_factory=session_factory,
                settings=settings,
                client=xrocket_client,
                bot=bot,
                i18n=i18n,
                service_channel=service_channel,
                metrics=metrics,
            ),
        )


async def main() -> None:
    settings = load_settings()
    settings.validate_runtime()
    setup_json_logging(settings)

    health_state = HealthState()
    task_runner = TaskRunner()
    engine = None
    redis_client = None
    fsm_storage = None
    apscheduler = None
    bot = None
    dispatcher = None
    cryptobot_client = None
    xrocket_client = None
    http_runner = None
    metrics = create_metrics()

    try:
        engine = create_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        redis_client = create_redis_client(settings.redis_url)
        bot = Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
        await resolve_bot_username(bot, settings)
        await configure_menu_icons(bot, settings)
        await configure_event_emoji(bot, settings)
        await configure_text_emoji(bot, settings)
        fsm_storage = RedisStorage.from_url(settings.redis_url)
        dispatcher = Dispatcher(storage=fsm_storage)
        i18n = JsonI18n(
            locales_dir=Path(__file__).resolve().parent / "locales",
            fallback_language=settings.bot.fallback_language,
        )
        await configure_commands(bot, i18n, settings)
        service_channel = ServiceChannel(bot, settings)
        cryptobot_client = build_cryptobot_client(settings)
        xrocket_client = build_xrocket_client(settings)
        dispatcher["settings"] = settings
        dispatcher["i18n"] = i18n
        dispatcher["session_factory"] = session_factory
        dispatcher["service_channel"] = service_channel
        dispatcher["cryptobot_client"] = cryptobot_client
        dispatcher["xrocket_client"] = xrocket_client
        dispatcher["metrics"] = metrics
        # Broadcast UI flow (aiogram-broadcast): interactive composer for /broadcast.
        # The send path copies the composed message to the segment ids passed to
        # open_menu(); the service/storage below are only the constructor deps the UI
        # needs, not used to pick recipients. Scheduled broadcasts persist in a Redis job
        # store and run via a module-level job, so they survive a restart.
        set_scheduled_broadcast_bot(bot)
        broadcast_service = BroadcastService(bot, RedisBroadcastStorage(redis_client))
        apscheduler = AsyncIOScheduler(
            jobstores={"default": create_redis_jobstore(settings.redis_url)}
        )
        apscheduler.start()
        broadcast_scheduler = BroadcastScheduler(broadcast_service, apscheduler)
        dispatcher.update.middleware.register(
            PersistentBroadcastUIMiddleware(broadcast_service, broadcast_scheduler)
        )
        # Stamp last_active_at (throttled) on every interaction so DAU/MAU is queryable.
        dispatcher.update.middleware.register(
            ActivityMiddleware(session_factory, redis_client)
        )
        # A command always clears a composer/admin-panel FSM state so it leaves the flow
        # instead of being swallowed and deleted by the state-gated handlers.
        dispatcher.message.outer_middleware.register(
            FsmCommandExitMiddleware(_FSM_EXIT_PREFIXES)
        )
        dispatcher.include_router(create_router())
        # Registered AFTER the app router so bot commands are always handled (not eaten by
        # the composer); only non-command input falls through to the FSM-state-gated UI.
        BroadcastUIHandlers().register(dispatcher)
        start_background_jobs(
            task_runner=task_runner,
            session_factory=session_factory,
            settings=settings,
            bot=bot,
            i18n=i18n,
            service_channel=service_channel,
            cryptobot_client=cryptobot_client,
            xrocket_client=xrocket_client,
            metrics=metrics,
        )

        bot_identity = BotIdentityCache(bot, ttl_seconds=settings.http.bot_cache_seconds)
        webhooks = _build_payment_webhooks(
            settings=settings,
            session_factory=session_factory,
            bot=bot,
            i18n=i18n,
            service_channel=service_channel,
            metrics=metrics,
            cryptobot_client=cryptobot_client,
            xrocket_client=xrocket_client,
        )
        http_app = create_http_app(
            HealthDependencies(
                check_db=lambda: check_database(engine),
                check_redis=lambda: check_redis(redis_client),
                check_bot=bot_identity.check,
            ),
            health_state,
            registry=metrics.registry,
            webhooks=webhooks,
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
        if apscheduler is not None:
            with suppress(Exception):
                apscheduler.shutdown(wait=False)
        if fsm_storage is not None:
            await fsm_storage.close()
        await close_payment_clients(
            cryptobot_client=cryptobot_client,
            xrocket_client=xrocket_client,
        )
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
