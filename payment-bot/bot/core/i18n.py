from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonI18n:
    def __init__(
        self,
        *,
        locales_dir: Path | None = None,
        fallback_language: str = "ru",
    ) -> None:
        self.locales_dir = locales_dir or Path(__file__).resolve().parents[1] / "locales"
        self.fallback_language = fallback_language
        self._translations = self._load_translations()

    @property
    def languages(self) -> set[str]:
        return set(self._translations)

    def gettext(self, key: str, language: str | None = None, **kwargs: Any) -> str:
        selected_language = self._resolve_language(language)
        template = self._translations[selected_language].get(key)
        if template is None and selected_language != self.fallback_language:
            template = self._translations[self.fallback_language].get(key)
        if template is None:
            return key
        return template.format(**kwargs)

    def _resolve_language(self, language: str | None) -> str:
        if language in self._translations:
            return language
        return self.fallback_language

    def _load_translations(self) -> dict[str, dict[str, str]]:
        translations: dict[str, dict[str, str]] = {}
        for path in sorted(self.locales_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as locale_file:
                data = json.load(locale_file)
            translations[path.stem] = {str(key): str(value) for key, value in data.items()}

        if self.fallback_language not in translations:
            msg = f"fallback language {self.fallback_language!r} is not available"
            raise ValueError(msg)
        return translations
