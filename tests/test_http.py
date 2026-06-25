from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.core.http import HealthDependencies, HealthState, create_http_app
from bot.core.metrics import create_metrics_registry


@asynccontextmanager
async def _client(app: web.Application) -> AsyncIterator[TestClient]:
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_health_ok() -> None:
    app = create_http_app(
        HealthDependencies(
            check_db=lambda: True,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        HealthState(),
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/health")
        payload = await response.json()

        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["db"] == "ok"


async def test_health_dependency_failure_returns_503() -> None:
    app = create_http_app(
        HealthDependencies(
            check_db=lambda: False,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        HealthState(),
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/health")
        payload = await response.json()

        assert response.status == 503
        assert payload["status"] == "degraded"
        assert payload["db"] == "error"


async def test_health_dependency_exception_returns_503() -> None:
    def fail_check() -> bool:
        raise RuntimeError("db unavailable")

    app = create_http_app(
        HealthDependencies(
            check_db=fail_check,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        HealthState(),
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/health")
        payload = await response.json()

        assert response.status == 503
        assert payload["status"] == "degraded"
        assert payload["db"] == "error"


async def test_health_accepts_async_dependencies() -> None:
    async def check_db() -> bool:
        return True

    app = create_http_app(
        HealthDependencies(
            check_db=check_db,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        HealthState(),
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/health")
        payload = await response.json()

        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["db"] == "ok"


async def test_health_shutdown_returns_503() -> None:
    state = HealthState()
    state.shutting_down = True
    app = create_http_app(
        HealthDependencies(
            check_db=lambda: True,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        state,
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/health")
        payload = await response.json()

        assert response.status == 503
        assert payload["status"] == "shutting_down"


async def test_metrics_endpoint() -> None:
    app = create_http_app(
        HealthDependencies(
            check_db=lambda: True,
            check_redis=lambda: True,
            check_bot=lambda: True,
        ),
        HealthState(),
        registry=create_metrics_registry(),
    )
    async with _client(app) as client:
        response = await client.get("/metrics")
        body = await response.text()

        assert response.status == 200
        assert "payment_bot_build_info" in body
        assert 'payment_bot_build_info{phase="8"} 1.0' in body
        assert "python_info" in body
        assert "process_cpu_seconds_total" in body
        assert "gc_objects_collected_total" in body
