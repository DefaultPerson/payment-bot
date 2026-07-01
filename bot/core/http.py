from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

CheckCallable = Callable[[], bool | Awaitable[bool]]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HealthDependencies:
    check_db: CheckCallable
    check_redis: CheckCallable
    check_bot: CheckCallable


@dataclass(slots=True)
class HealthState:
    shutting_down: bool = False


HEALTH_DEPENDENCIES = web.AppKey("health_dependencies", HealthDependencies)
HEALTH_STATE = web.AppKey("health_state", HealthState)
METRICS_REGISTRY = web.AppKey("metrics_registry", CollectorRegistry)


async def _call_check(check: CheckCallable) -> bool:
    result = check()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


async def _health(request: web.Request) -> web.Response:
    dependencies = request.app[HEALTH_DEPENDENCIES]
    state = request.app[HEALTH_STATE]

    if state.shutting_down:
        return web.json_response(
            {
                "status": "shutting_down",
                "db": "unknown",
                "redis": "unknown",
                "bot": "unknown",
                "ts": datetime.now(UTC).isoformat(),
            },
            status=503,
        )

    checks: dict[str, bool] = {}
    for key, check in {
        "db": dependencies.check_db,
        "redis": dependencies.check_redis,
        "bot": dependencies.check_bot,
    }.items():
        try:
            checks[key] = await _call_check(check)
        except Exception:
            logger.warning("health dependency check failed", extra={"dependency": key})
            checks[key] = False

    healthy = all(checks.values())
    payload: dict[str, Any] = {
        "status": "ok" if healthy else "degraded",
        "db": "ok" if checks["db"] else "error",
        "redis": "ok" if checks["redis"] else "error",
        "bot": "ok" if checks["bot"] else "error",
        "ts": datetime.now(UTC).isoformat(),
    }
    return web.json_response(payload, status=200 if healthy else 503)


async def _metrics(request: web.Request) -> web.Response:
    registry = request.app[METRICS_REGISTRY]
    return web.Response(
        body=generate_latest(registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


def create_http_app(
    dependencies: HealthDependencies,
    state: HealthState,
    *,
    registry: CollectorRegistry,
    tribute_webhook: tuple[str, Callable[[web.Request], Awaitable[web.StreamResponse]]]
    | None = None,
) -> web.Application:
    app = web.Application()
    app[HEALTH_DEPENDENCIES] = dependencies
    app[HEALTH_STATE] = state
    app[METRICS_REGISTRY] = registry
    app.router.add_get("/health", _health)
    app.router.add_get("/metrics", _metrics)
    if tribute_webhook is not None:
        path, handler = tribute_webhook
        app.router.add_post(path, handler)
    return app
