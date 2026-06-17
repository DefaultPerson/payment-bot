import inspect
from dataclasses import dataclass
from typing import get_type_hints

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.handlers import create_router
from bot.handlers.start import build_start_text, start


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


def test_build_start_text_maps_telegram_uk_to_ukrainian_locale() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "uk")

    assert "Ласкаво просимо до платіжного бота Hydra Monitors." in text
    assert "https://t.me/HydraSupport" in text


def test_build_start_text_maps_regional_english_tag_to_english_locale() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "en-US")

    assert "Welcome to Hydra Monitors payment bot." in text
    assert "https://t.me/HydraSupport" in text


def test_create_router_does_not_require_dependencies() -> None:
    router = create_router()

    assert router.name == "handlers"


def test_start_handler_has_aiogram_di_compatible_signature() -> None:
    signature = inspect.signature(start)
    type_hints = get_type_hints(start)

    assert list(signature.parameters) == ["message", "settings", "i18n"]
    assert type_hints["settings"] is Settings
    assert type_hints["i18n"] is JsonI18n


async def test_start_handler_uses_injected_dependencies() -> None:
    @dataclass(slots=True)
    class User:
        language_code: str

    class FakeMessage:
        from_user = User(language_code="en-US")

        def __init__(self) -> None:
            self.answer_text: str | None = None

        async def answer(self, text: str) -> None:
            self.answer_text = text

    message = FakeMessage()
    settings = Settings(bot={"support_username": "HydraSupport"})

    await start(message, settings=settings, i18n=JsonI18n())

    assert message.answer_text is not None
    assert "Welcome to Hydra Monitors payment bot." in message.answer_text
    assert "https://t.me/HydraSupport" in message.answer_text
