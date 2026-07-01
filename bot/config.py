from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
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
    username: str = Field(default="HydraPaymentBot", min_length=1)  # resolved at runtime from getMe
    username_override: str = ""  # force a specific @username, skips getMe
    support_username: str = "HMonitorsBot"
    custom_emoji_icons: bool = True  # attach tgiosicons custom-emoji icons to inline buttons

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


class CryptoBotConfig(BaseModel):
    enabled: bool = True
    token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("token", "CRYPTOBOT_TOKEN"),
    )
    api_url: str = "https://pay.crypt.bot"
    testnet_api_url: str = "https://testnet-pay.crypt.bot"  # used when payments.test_mode is on
    accepted_assets: list[str] = Field(default_factory=lambda: ["USDT", "TON", "BTC"])
    fee_percent: Decimal = Field(default=Decimal("3"), ge=0, lt=100)
    invoice_expires_seconds: int = Field(default=3600, ge=1, le=2_678_400)
    poll_interval_seconds: int = Field(default=30, ge=1)
    poll_batch_size: int = Field(default=50, ge=1, le=100)
    webhook_path: str = "/webhooks/cryptobot"  # Crypto Pay webhook (poller stays as fallback)

    @field_validator("token", mode="before")
    @classmethod
    def blank_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)


class XRocketConfig(BaseModel):
    enabled: bool = False
    token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("token", "XROCKET_TOKEN"),
    )
    testnet: bool = False
    base_url: str | None = None
    # The aiorocket2 SDK's built-in testnet host (pay.testnet.xrocket.tg) is dead (TLS
    # handshake failure). When payments.test_mode is on we override base_url with this
    # working testnet endpoint instead.
    testnet_base_url: str = "https://pay.testnet.xrocket.exchange"
    currency: str = Field(default="USDT", min_length=1)
    fee_percent: Decimal = Field(default=Decimal("0"), ge=0, lt=100)
    invoice_expires_seconds: int = Field(default=3600, ge=1, le=86_400)
    poll_interval_seconds: int = Field(default=30, ge=1)
    poll_batch_size: int = Field(default=50, ge=1, le=100)
    webhook_path: str = "/webhooks/xrocket"  # inbound xRocket webhook (poller stays as fallback)

    @field_validator("token", mode="before")
    @classmethod
    def blank_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)


class TributeProductConfig(BaseModel):
    """Maps a Tribute digital product to a local tariff.

    Tribute has no create-product API, so products are created manually in the Tribute
    dashboard. ``product_id`` is echoed back in the ``new_digital_product`` webhook (used
    to resolve the tariff); ``payment_link`` is the checkout URL opened by the pay button.
    """

    product_id: int = Field(ge=1)
    tariff_code: str = Field(min_length=1)
    payment_link: str = Field(min_length=1)


class TributeConfig(BaseModel):
    """Tribute payment method (cards / crypto on Tribute's side, one-time purchases only).

    Settlement arrives via the ``new_digital_product`` webhook, HMAC-SHA256 signed with the
    API key. No sandbox exists, so the test path uses a real (cheap) product mapped to the
    ``test_only`` tariff. Tribute prices are fixed in the dashboard, so loyalty/promo do not
    apply to them.
    """

    enabled: bool = False
    token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("token", "TRIBUTE_API_KEY"),
    )
    webhook_path: str = "/webhooks/tribute"
    products: list[TributeProductConfig] = Field(default_factory=list)

    @field_validator("token", mode="before")
    @classmethod
    def blank_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)

    def tariff_for_product(self, product_id: int) -> str | None:
        for product in self.products:
            if product.product_id == product_id:
                return product.tariff_code
        return None

    def link_for_tariff(self, tariff_code: str) -> str | None:
        for product in self.products:
            if product.tariff_code == tariff_code:
                return product.payment_link
        return None


class TrialConfig(BaseModel):
    """One-time free trial (no payment).

    When ``enabled``, an eligible user can start a single trial that grants the plan/chats of
    ``tariff_code`` for ``duration_days`` days. ``tariff_code`` only picks the plan/access
    scope (default the cheapest real tariff); the length comes from ``duration_days``, not the
    tariff's own duration. Test-only tariffs are hidden outside test_mode, so the default
    points at a real, always-present code (``1m``).
    """

    enabled: bool = False
    tariff_code: str = Field(default="1m", min_length=1)
    duration_days: int = Field(default=3, ge=1)


class PaymentsConfig(BaseModel):
    """Master switch: route live providers to their TEST networks.

    When ``test_mode`` is on, CryptoBot uses ``cryptobot.testnet_api_url`` with
    ``CRYPTOBOT_TESTNET_TOKEN`` and xRocket points ``base_url`` at
    ``xrocket.testnet_base_url`` using ``XROCKET_TESTNET_TOKEN`` (applied in Settings
    resolution). Telegram Stars has no test network and is unaffected.
    """

    test_mode: bool = False


class PlanConfig(BaseModel):
    """A subscription product. Groups duration variants and the chats it unlocks.

    ``chat_ids`` empty = the plan opens every configured ``access.protected_chats``
    (the current single-plan behaviour). Listing ids restricts the plan to that subset.
    """

    code: str
    title: str = ""
    description: str = ""
    chat_ids: list[int] = Field(default_factory=list)
    active: bool = True


class TariffConfig(BaseModel):
    code: str
    plan_code: str = "main"
    duration_months: int = Field(ge=1)
    base_price_usd: Decimal = Field(gt=0)
    duration_discount_percent: Decimal = Field(default=Decimal("0"), ge=0, lt=100)
    active: bool = True
    test_only: bool = False  # selectable only when payments.test_mode is on
    stars_amount_override: int | None = Field(default=None, ge=1)  # pin XTR price (1 for tests)


class TariffsConfig(BaseModel):
    plans: list[PlanConfig] = Field(
        # Defaults stay generic (empty chat_ids = "opens every protected chat"). Per-channel
        # scoping (main = base channel only; pro = base + exclusive PRO channel) is a
        # deployment concern delivered via config.toml locally and the TARIFFS__PLANS env
        # override in prod — never hardcode infra channel ids into these code defaults.
        default_factory=lambda: [
            PlanConfig(code="main", title="Hydra Monitors"),
            PlanConfig(code="pro", title="HMonitors PRO"),
        ],
    )
    items: list[TariffConfig] = Field(
        default_factory=lambda: [
            TariffConfig(
                code="1m",
                plan_code="main",
                duration_months=1,
                base_price_usd=Decimal("59"),
            ),
            TariffConfig(
                code="3m",
                plan_code="main",
                duration_months=3,
                base_price_usd=Decimal("177"),
                duration_discount_percent=Decimal("5"),
            ),
            TariffConfig(
                code="6m",
                plan_code="main",
                duration_months=6,
                base_price_usd=Decimal("354"),
                duration_discount_percent=Decimal("10"),
            ),
            # Cheap end-to-end payment test. test_only -> auto-hidden unless
            # payments.test_mode is on; stars_amount_override pins the Stars price to 1 XTR.
            # duration_months >= 2 keeps Stars one-shot (no recurring auto-rebill).
            TariffConfig(
                code="test",
                plan_code="main",
                duration_months=3,
                base_price_usd=Decimal("1.00"),
                test_only=True,
                stars_amount_override=1,
            ),
            # Same cheap E2E probe for the PRO plan/channel. No real PRO tariff exists yet,
            # so outside test_mode the PRO product shows an empty duration menu until priced.
            TariffConfig(
                code="pro_test",
                plan_code="pro",
                duration_months=3,
                base_price_usd=Decimal("1.00"),
                test_only=True,
                stars_amount_override=1,
            ),
        ],
    )

    @model_validator(mode="after")
    def validate_codes(self) -> TariffsConfig:
        if not self.plans:
            msg = "at least one tariff plan must be configured"
            raise ValueError(msg)

        plan_codes: set[str] = set()
        plan_duplicates: set[str] = set()
        for plan in self.plans:
            if plan.code in plan_codes:
                plan_duplicates.add(plan.code)
            plan_codes.add(plan.code)
        if plan_duplicates:
            msg = "plan codes must be unique"
            raise ValueError(msg)

        seen: set[str] = set()
        duplicates: set[str] = set()
        for tariff in self.items:
            if tariff.code in seen:
                duplicates.add(tariff.code)
            seen.add(tariff.code)
        if duplicates:
            msg = "tariff codes must be unique"
            raise ValueError(msg)

        unknown_plans = sorted(
            {tariff.plan_code for tariff in self.items if tariff.plan_code not in plan_codes}
        )
        if unknown_plans:
            joined = ", ".join(unknown_plans)
            msg = f"tariff plan_code must reference a configured plan: {joined}"
            raise ValueError(msg)
        return self

    @property
    def default_plan_code(self) -> str:
        for plan in self.plans:
            if plan.active:
                return plan.code
        return self.plans[0].code if self.plans else "main"


class LoyaltyConfig(BaseModel):
    """Loyalty tiers as percentage discounts off the (duration-discounted) base price.

    Tenure is counted in MONTHS: ``*_active_months`` is the 1-indexed month of tenure the
    tier starts from (month 1 = the first 30 active days = basic, no loyalty discount).
    Defaults: bronze 5% from month 2, silver 10% from month 4, gold 15% from month 6.
    """

    bronze_active_months: int = Field(default=2, ge=2)
    bronze_discount_percent: Decimal = Field(default=Decimal("5"), ge=0, lt=100)
    silver_active_months: int = Field(default=4, ge=2)
    silver_discount_percent: Decimal = Field(default=Decimal("10"), ge=0, lt=100)
    gold_active_months: int = Field(default=6, ge=2)
    gold_discount_percent: Decimal = Field(default=Decimal("15"), ge=0, lt=100)

    @model_validator(mode="after")
    def validate_tiers(self) -> LoyaltyConfig:
        if not (self.bronze_active_months < self.silver_active_months < self.gold_active_months):
            msg = "loyalty active_months must strictly increase: bronze < silver < gold"
            raise ValueError(msg)
        if not (
            self.bronze_discount_percent
            <= self.silver_discount_percent
            <= self.gold_discount_percent
        ):
            msg = "loyalty discount percents must be non-decreasing: bronze <= silver <= gold"
            raise ValueError(msg)
        return self


class PromosConfig(BaseModel):
    max_discount_percent: Decimal = Field(default=Decimal("50"), ge=0, le=100)


class StarsConfig(BaseModel):
    enabled: bool = True
    app_store_fee_percent: Decimal = Field(default=Decimal("30"), ge=0, lt=100)
    star_usd_rate: Decimal = Field(default=Decimal("0.013"), gt=0)
    invoice_expires_seconds: int = Field(default=3600, ge=1)


class ReferralConfig(BaseModel):
    percent_default: Decimal = Field(default=Decimal("10"), ge=0, lt=100)
    min_withdraw_usd: Decimal = Field(default=Decimal("10"), gt=0)
    allow_referrer_change: bool = False


class NotificationsConfig(BaseModel):
    loop_interval_seconds: int = Field(default=3600, ge=1)
    batch_size: int = Field(default=100, ge=1, le=1000)
    reminder_days_before: int = Field(default=3, ge=1)
    reminder_window_seconds: int = Field(default=3600, ge=1)
    post_expired_delay_hours: int = Field(default=24, ge=1)


class ObservabilityConfig(BaseModel):
    invoice_cleanup_interval_seconds: int = Field(default=300, ge=1)
    metrics_snapshot_interval_seconds: int = Field(default=60, ge=1)


class ProtectedChatConfig(BaseModel):
    chat_id: int
    title: str


class AccessConfig(BaseModel):
    protected_chats: list[ProtectedChatConfig] = Field(default_factory=list)
    invite_link_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60)
    revoke_ban_seconds: int = Field(default=60, ge=1)


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
    cryptobot_token: SecretStr | None = Field(
        default=None,
        validation_alias="CRYPTOBOT_TOKEN",
        exclude=True,
    )
    xrocket_token: SecretStr | None = Field(
        default=None,
        validation_alias="XROCKET_TOKEN",
        exclude=True,
    )
    cryptobot_testnet_token: SecretStr | None = Field(
        default=None,
        validation_alias="CRYPTOBOT_TESTNET_TOKEN",
        exclude=True,
    )
    xrocket_testnet_token: SecretStr | None = Field(
        default=None,
        validation_alias="XROCKET_TESTNET_TOKEN",
        exclude=True,
    )
    tribute_token: SecretStr | None = Field(
        default=None,
        validation_alias="TRIBUTE_API_KEY",
        exclude=True,
    )

    bot: BotConfig = Field(default_factory=BotConfig)
    service_channel: ServiceChannelConfig = Field(default_factory=ServiceChannelConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    cryptobot: CryptoBotConfig = Field(default_factory=CryptoBotConfig)
    xrocket: XRocketConfig = Field(default_factory=XRocketConfig)
    tariffs: TariffsConfig = Field(default_factory=TariffsConfig)
    loyalty: LoyaltyConfig = Field(default_factory=LoyaltyConfig)
    promos: PromosConfig = Field(default_factory=PromosConfig)
    stars: StarsConfig = Field(default_factory=StarsConfig)
    referral: ReferralConfig = Field(default_factory=ReferralConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    access: AccessConfig = Field(default_factory=AccessConfig)
    payments: PaymentsConfig = Field(default_factory=PaymentsConfig)
    tribute: TributeConfig = Field(default_factory=TributeConfig)
    trial: TrialConfig = Field(default_factory=TrialConfig)

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

    @field_validator("cryptobot_token", mode="before")
    @classmethod
    def blank_cryptobot_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)

    @field_validator("xrocket_token", mode="before")
    @classmethod
    def blank_xrocket_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)

    @field_validator("cryptobot_testnet_token", "xrocket_testnet_token", mode="before")
    @classmethod
    def blank_testnet_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)

    @field_validator("tribute_token", mode="before")
    @classmethod
    def blank_tribute_token_is_none(cls, value: Any) -> Any:
        return _blank_secret_to_none(value)

    @model_validator(mode="after")
    def apply_flat_aliases_and_validate_provider_tokens(self) -> Settings:
        if self.service_channel_id is not None:
            self.service_channel.chat_id = self.service_channel_id
        if self.service_thread_id is not None:
            self.service_channel.thread_id = self.service_thread_id
        if self.cryptobot_token is not None:
            self.cryptobot.token = self.cryptobot_token
        if self.xrocket_token is not None:
            self.xrocket.token = self.xrocket_token
        if self.tribute_token is not None:
            self.tribute.token = self.tribute_token
        if self.payments.test_mode:
            self._apply_payments_test_mode()
        self._filter_test_only_tariffs()
        self._validate_cryptobot_token()
        self._validate_xrocket_token()
        self._validate_tribute_token()
        return self

    def _apply_payments_test_mode(self) -> None:
        """Route live providers to their test networks (Stars has none)."""
        self.cryptobot.api_url = self.cryptobot.testnet_api_url
        if self.cryptobot_testnet_token is not None:
            self.cryptobot.token = self.cryptobot_testnet_token
        self.xrocket.testnet = True
        self.xrocket.base_url = self.xrocket.testnet_base_url
        if self.xrocket_testnet_token is not None:
            self.xrocket.token = self.xrocket_testnet_token

    def _filter_test_only_tariffs(self) -> None:
        """Hide test-only tariffs outside test mode so they can't be sold in production."""
        if self.payments.test_mode:
            return
        self.tariffs.items = [tariff for tariff in self.tariffs.items if not tariff.test_only]

    def _validate_cryptobot_token(self) -> None:
        if self.cryptobot.token is not None:
            token = self.cryptobot.token.get_secret_value()
            if not token.strip():
                msg = "CRYPTOBOT_TOKEN must not be empty when set"
                raise ValueError(msg)

    def _validate_xrocket_token(self) -> None:
        if self.xrocket.token is not None:
            token = self.xrocket.token.get_secret_value()
            if not token.strip():
                msg = "XROCKET_TOKEN must not be empty when set"
                raise ValueError(msg)

    def _validate_tribute_token(self) -> None:
        if self.tribute.token is not None:
            token = self.tribute.token.get_secret_value()
            if not token.strip():
                msg = "TRIBUTE_API_KEY must not be empty when set"
                raise ValueError(msg)

    def _validate_test_mode_tokens(self) -> None:
        if self.cryptobot.enabled and self.cryptobot_testnet_token is None:
            msg = (
                "CRYPTOBOT_TESTNET_TOKEN must be set when "
                "payments.test_mode is on and cryptobot is enabled"
            )
            raise ValueError(msg)
        if self.xrocket.enabled and self.xrocket_testnet_token is None:
            msg = (
                "XROCKET_TESTNET_TOKEN must be set when "
                "payments.test_mode is on and xrocket is enabled"
            )
            raise ValueError(msg)

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
        if self.payments.test_mode:
            self._validate_test_mode_tokens()
        self._validate_cryptobot_token()
        self._validate_xrocket_token()
        self._validate_tribute_token()
        if self.tribute.enabled:
            if self.tribute.token is None:
                msg = "TRIBUTE_API_KEY must be set when tribute is enabled"
                raise ValueError(msg)
            if not self.tribute.products:
                msg = "tribute.products must not be empty when tribute is enabled"
                raise ValueError(msg)
        if self.trial.enabled:
            # test_only tariffs are already filtered out above unless test_mode is on, so this
            # also rejects a trial pointing at a hidden test tariff in production.
            active_codes = {tariff.code for tariff in self.tariffs.items if tariff.active}
            if self.trial.tariff_code not in active_codes:
                msg = f"trial.tariff_code must reference an active tariff: {self.trial.tariff_code}"
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


def _blank_secret_to_none(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return None if not value.get_secret_value().strip() else value
    if isinstance(value, str) and not value.strip():
        return None
    return value
