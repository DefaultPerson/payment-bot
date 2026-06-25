from __future__ import annotations

import json
from io import StringIO

import pytest


def test_run_load_smoke_returns_deterministic_summary() -> None:
    from bot.tools.load_smoke import run_load_smoke

    summary = run_load_smoke(iterations=3)

    assert summary.iterations == 3
    assert summary.payments_recorded == 3
    assert summary.provider_polls_recorded == 6
    assert summary.broadcast_recipients_recorded == 6
    assert summary.subscriptions_active == 3
    assert summary.metrics_bytes > 0


def test_run_load_smoke_rejects_empty_iteration_count() -> None:
    from bot.tools.load_smoke import run_load_smoke

    with pytest.raises(ValueError, match="iterations must be positive"):
        run_load_smoke(iterations=0)


def test_load_smoke_cli_prints_json_summary() -> None:
    from bot.tools.load_smoke import main

    stdout = StringIO()
    exit_code = main(["--iterations", "2"], stdout=stdout)

    body = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert body["iterations"] == 2
    assert body["payments_recorded"] == 2
    assert body["provider_polls_recorded"] == 4
    assert body["broadcast_recipients_recorded"] == 4
    assert body["subscriptions_active"] == 2
