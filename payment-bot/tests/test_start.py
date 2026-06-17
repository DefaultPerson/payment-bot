from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.handlers.start import build_start_text


def test_build_start_text_uses_english_for_english_user_language() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "en")

    assert "Welcome to Hydra Monitors payment bot." in text
    assert "https://t.me/HydraSupport" in text


def test_build_start_text_falls_back_to_russian_for_unsupported_language() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "de")

    assert "Добро пожаловать в платежного бота Hydra Monitors." in text
    assert "https://t.me/HydraSupport" in text
