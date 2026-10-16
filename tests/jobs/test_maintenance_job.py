from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from bot.config import Settings

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self.session)


class FakeMetrics:
    def __init__(self) -> None:
        self.active_values: list[int] = []

    def set_subscriptions_active(self, value: int) -> None:
        self.active_values.append(value)


async def test_invoice_cleanup_once_marks_expired_invoices_and_commits(monkeypatch) -> None:
    import bot.jobs.maintenance as maintenance_module

    session = FakeSession()
    seen: dict[str, Any] = {}

    async def fake_mark_expired_active_invoices(*args: Any, **kwargs: Any) -> int:
        seen["session"] = args[0]
        seen["now"] = kwargs["now"]
        return 3

    monkeypatch.setattr(
        maintenance_module,
        "mark_expired_active_invoices",
        fake_mark_expired_active_invoices,
    )

    count = await maintenance_module.invoice_cleanup_once(
        session_factory=FakeSessionFactory(session),
        now=NOW,
    )

    assert count == 3
    assert seen == {"session": session, "now": NOW}
    assert session.commits == 1
    assert session.rollbacks == 0


async def test_metrics_snapshot_once_updates_active_subscription_gauge(monkeypatch) -> None:
    import bot.jobs.maintenance as maintenance_module

    session = FakeSession()
    metrics = FakeMetrics()
    seen: dict[str, Any] = {}

    async def fake_count_active_subscriptions(*args: Any, **kwargs: Any) -> int:
        seen["session"] = args[0]
        seen["now"] = kwargs["now"]
        return 7

    monkeypatch.setattr(
        maintenance_module,
        "count_active_subscriptions",
        fake_count_active_subscriptions,
    )

    count = await maintenance_module.metrics_snapshot_once(
        session_factory=FakeSessionFactory(session),
        metrics=metrics,
        now=NOW,
    )

    assert count == 7
    assert metrics.active_values == [7]
    assert seen == {"session": session, "now": NOW}
    assert session.commits == 1
    assert session.rollbacks == 0


async def test_invoice_cleanup_once_rolls_back_on_cleanup_error(monkeypatch) -> None:
    import bot.jobs.maintenance as maintenance_module

    session = FakeSession()

    async def fail_cleanup(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("db failed")

    monkeypatch.setattr(maintenance_module, "mark_expired_active_invoices", fail_cleanup)

    with pytest.raises(RuntimeError, match="db failed"):
        await maintenance_module.invoice_cleanup_once(
            session_factory=FakeSessionFactory(session),
            now=NOW,
        )

    assert session.commits == 0
    assert session.rollbacks == 1


async def test_invoice_cleanup_loop_continues_after_iteration_error(monkeypatch) -> None:
    import bot.jobs.maintenance as maintenance_module

    calls = 0
    sleeps: list[int] = []

    async def fake_invoice_cleanup_once(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected failure")
        return 0

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        maintenance_module,
        "invoice_cleanup_once",
        fake_invoice_cleanup_once,
    )

    await maintenance_module.invoice_cleanup_loop(
        session_factory=FakeSessionFactory(FakeSession()),
        settings=Settings(observability={"invoice_cleanup_interval_seconds": 5}),
        sleep=fake_sleep,
        max_iterations=2,
    )

    assert calls == 2
    assert sleeps == [5]


async def test_metrics_snapshot_loop_re_raises_cancellation(monkeypatch) -> None:
    import bot.jobs.maintenance as maintenance_module

    async def cancel_snapshot(*args: Any, **kwargs: Any) -> int:
        raise asyncio.CancelledError

    async def fail_sleep(seconds: int) -> None:
        raise AssertionError(f"unexpected sleep {seconds}")

    monkeypatch.setattr(
        maintenance_module,
        "metrics_snapshot_once",
        cancel_snapshot,
    )

    with pytest.raises(asyncio.CancelledError):
        await maintenance_module.metrics_snapshot_loop(
            session_factory=FakeSessionFactory(FakeSession()),
            settings=Settings(),
            metrics=FakeMetrics(),
            sleep=fail_sleep,
        )
