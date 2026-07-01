from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bot.config import Settings


def _runtime_settings(**trial: Any) -> Settings:
    return Settings(bot_token="real-bot-token", service_channel_id=100, trial=trial)


def test_trial_defaults_are_disabled_and_point_at_cheapest_real_tariff() -> None:
    settings = Settings()

    assert settings.trial.enabled is False
    assert settings.trial.tariff_code == "1m"
    assert settings.trial.duration_days == 3


def test_trial_duration_days_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(trial={"duration_days": 0})


def test_validate_runtime_accepts_enabled_trial_with_known_tariff() -> None:
    settings = _runtime_settings(enabled=True, tariff_code="1m", duration_days=3)

    settings.validate_runtime()  # must not raise


def test_validate_runtime_rejects_enabled_trial_with_unknown_tariff() -> None:
    settings = _runtime_settings(enabled=True, tariff_code="nope")

    with pytest.raises(ValueError, match="trial.tariff_code must reference an active tariff"):
        settings.validate_runtime()


def test_validate_runtime_rejects_trial_pointing_at_hidden_test_tariff_in_prod() -> None:
    # `test` is a test_only tariff, filtered out of items outside test_mode, so a prod trial
    # pointed at it must fail validation rather than silently referencing a hidden tariff.
    settings = _runtime_settings(enabled=True, tariff_code="test")

    with pytest.raises(ValueError, match="trial.tariff_code must reference an active tariff"):
        settings.validate_runtime()


def test_disabled_trial_skips_tariff_validation() -> None:
    settings = _runtime_settings(enabled=False, tariff_code="does-not-exist")

    settings.validate_runtime()  # disabled -> tariff_code is not checked
