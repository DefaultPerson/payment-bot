from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class BotConfig(BaseModel):
    supported_languages: list[str] = Field(default_factory=lambda: ["ru", "ua", "en"])
    fallback_language: str = "ru"
    support_username: str = "HMonitorsBot"

    @model_validator(mode="after")
    def validate_fallback_language(self) -> BotConfig:
        if self.fallback_language not in self.supported_languages:
            msg = "fallback_language must be present in supported_languages"
            raise ValueError(msg)
        return self


class ServiceChannelConfig(BaseModel):
    chat_id: int | None = None
    thread_id: int | None = None


class HttpConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    bot_cache_seconds: int = Field(default=30, ge=1)


class LoggingConfig(BaseModel):
    file_path: Path = Path("logs/payment-bot.log")
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=5, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: SecretStr = Field(default=SecretStr("test-token"), validation_alias="BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:pb@localhost:5432/payment_bot",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        validation_alias="ADMIN_IDS",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    service_channel_id: int | None = Field(
        default=None,
        validation_alias="SERVICE_CHANNEL_ID",
        exclude=True,
    )
    service_thread_id: int | None = Field(
        default=None,
        validation_alias="SERVICE_THREAD_ID",
        exclude=True,
    )

    bot: BotConfig = Field(default_factory=BotConfig)
    service_channel: ServiceChannelConfig = Field(default_factory=ServiceChannelConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: Any) -> list[int] | Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in valid:
            msg = f"LOG_LEVEL must be one of {sorted(valid)}"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def apply_flat_service_channel_aliases(self) -> Settings:
        if self.service_channel_id is not None:
            self.service_channel.chat_id = self.service_channel_id
        if self.service_thread_id is not None:
            self.service_channel.thread_id = self.service_thread_id
        return self

    def validate_runtime(self) -> None:
        bot_token = self.bot_token.get_secret_value()
        if bot_token == "test-token" or "replace-me" in bot_token:
            msg = "BOT_TOKEN must be set for runtime startup"
            raise ValueError(msg)
        if self.service_channel.chat_id is None:
            msg = (
                "SERVICE_CHANNEL__CHAT_ID, SERVICE_CHANNEL_ID, "
                "or service_channel.chat_id must be set"
            )
            raise ValueError(msg)
        if not self.database_url.startswith("postgresql+asyncpg://"):
            msg = "DATABASE_URL must use postgresql+asyncpg://"
            raise ValueError(msg)


def load_settings(
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = ".env",
) -> Settings:
    path = Path(config_path or os.getenv("CONFIG_PATH", "config.toml"))
    settings_model_config = {**Settings.model_config, "toml_file": path}

    class TomlSettings(Settings):
        model_config = SettingsConfigDict(**settings_model_config)

    return TomlSettings(_env_file=env_file)
