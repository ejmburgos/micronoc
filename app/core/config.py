import json
from functools import lru_cache

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALL_ALERT_CODES = (
    "smartolt_unavailable",
    "smartolt_onu_loss",
    "smartolt_onu_pwrfail",
    "smartolt_low_signal",
    "router_unreachable",
    "router_recovered",
    "router_overload",
    "router_processing_overload",
    "upstream_congestion",
    "wan_congestion",
    "wan_low_traffic",
    "link_saturation",
    "link_flapping",
)


class MikroTikRouterConfig(BaseModel):
    name: str
    host: str
    port: int = 8728
    user: str
    password: str
    role: str = "access"
    wan_interface: str | None = None
    link_capacity_bps: int | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="micronoc", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    app_public_url: str = Field(default="", validation_alias="APP_PUBLIC_URL")
    monitor_interval_seconds: int = Field(default=30, validation_alias="MONITOR_INTERVAL_SECONDS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    timezone: str = Field(default="America/Argentina/Cordoba", validation_alias="TIMEZONE")
    debug: bool = Field(default=False, validation_alias="DEBUG")

    smartolt_base_url: str = Field(default="", validation_alias="SMARTOLT_BASE_URL")
    smartolt_api_key: str = Field(default="", validation_alias="SMARTOLT_API_KEY")
    smartolt_health_path: str = Field(default="/health", validation_alias="SMARTOLT_HEALTH_PATH")
    smartolt_kpis_path: str = Field(default="", validation_alias="SMARTOLT_KPIS_PATH")
    smartolt_site_name: str = Field(default="SmartOLT", validation_alias="SMARTOLT_SITE_NAME")

    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_db: str = Field(default="micronoc", validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="micronoc", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="", validation_alias="POSTGRES_PASSWORD")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    mikrotik_host: str = Field(default="", validation_alias="MIKROTIK_HOST")
    mikrotik_port: int = Field(default=8728, validation_alias="MIKROTIK_PORT")
    mikrotik_user: str = Field(default="", validation_alias="MIKROTIK_USER")
    mikrotik_password: str = Field(default="", validation_alias="MIKROTIK_PASSWORD")
    mikrotik_routers_json: str | None = Field(default=None, validation_alias="MIKROTIK_ROUTERS_JSON")
    diag_cpu_warning_threshold: int = Field(default=80, validation_alias="DIAG_CPU_WARNING_THRESHOLD")
    diag_wan_bps_warning_threshold: int = Field(
        default=800000000,
        validation_alias="DIAG_WAN_BPS_WARNING_THRESHOLD",
    )
    diag_wan_low_traffic_threshold_bps: int = Field(
        default=1000000,
        validation_alias="DIAG_WAN_LOW_TRAFFIC_THRESHOLD_BPS",
    )
    diag_wan_low_traffic_consecutive_samples: int = Field(
        default=3,
        validation_alias="DIAG_WAN_LOW_TRAFFIC_CONSECUTIVE_SAMPLES",
    )
    diag_flap_window_minutes: int = Field(default=5, validation_alias="DIAG_FLAP_WINDOW_MINUTES")
    diag_flap_threshold: int = Field(default=3, validation_alias="DIAG_FLAP_THRESHOLD")
    diag_smartolt_onu_loss_threshold: int = Field(default=5, validation_alias="DIAG_SMARTOLT_ONU_LOSS_THRESHOLD")
    diag_smartolt_offline_los_threshold: int = Field(
        default=5,
        validation_alias="DIAG_SMARTOLT_OFFLINE_LOS_THRESHOLD",
    )
    diag_smartolt_offline_pwrfail_threshold: int = Field(
        default=50,
        validation_alias="DIAG_SMARTOLT_OFFLINE_PWRFAIL_THRESHOLD",
    )
    diag_smartolt_low_signal_threshold: int = Field(
        default=1,
        validation_alias="DIAG_SMARTOLT_LOW_SIGNAL_THRESHOLD",
    )
    dashboard_stale_seconds: int = Field(default=120, validation_alias="DASHBOARD_STALE_SECONDS")
    dashboard_feature_monitoring_tab: bool = Field(default=True, validation_alias="DASHBOARD_FEATURE_MONITORING_TAB")
    dashboard_feature_settings_tab: bool = Field(default=True, validation_alias="DASHBOARD_FEATURE_SETTINGS_TAB")
    dashboard_feature_webfig_tab: bool = Field(default=True, validation_alias="DASHBOARD_FEATURE_WEBFIG_TAB")
    dashboard_feature_smartolt_tab: bool = Field(default=True, validation_alias="DASHBOARD_FEATURE_SMARTOLT_TAB")
    dashboard_feature_threshold_settings: bool = Field(
        default=True,
        validation_alias="DASHBOARD_FEATURE_THRESHOLD_SETTINGS",
    )
    dashboard_feature_threshold_edit: bool = Field(default=True, validation_alias="DASHBOARD_FEATURE_THRESHOLD_EDIT")
    webfig_base_url: str = Field(default="", validation_alias="WEBFIG_BASE_URL")
    webfig_username: str = Field(default="", validation_alias="WEBFIG_USERNAME")
    webfig_password: str = Field(default="", validation_alias="WEBFIG_PASSWORD")
    diag_enabled_alert_codes: str = Field(
        default=",".join(ALL_ALERT_CODES),
        validation_alias="DIAG_ENABLED_ALERT_CODES",
    )
    telegram_enabled: bool = Field(default=False, validation_alias="TELEGRAM_ENABLED")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")
    telegram_alert_cooldown_seconds: int = Field(
        default=300,
        validation_alias="TELEGRAM_ALERT_COOLDOWN_SECONDS",
    )
    telegram_alert_codes: str = Field(
        default="router_unreachable",
        validation_alias="TELEGRAM_ALERT_CODES",
    )
    telegram_alert_title: str = Field(default="ALERTA NOC BVCOM", validation_alias="TELEGRAM_ALERT_TITLE")
    telegram_window_start_hour: int = Field(default=6, validation_alias="TELEGRAM_WINDOW_START_HOUR")
    telegram_window_end_hour: int = Field(default=23, validation_alias="TELEGRAM_WINDOW_END_HOUR")

    @field_validator("smartolt_base_url", mode="before")
    @classmethod
    def normalize_smartolt_base_url(cls, value: str | None) -> str:
        if not value:
            return ""
        return str(value).rstrip("/")

    @field_validator("webfig_base_url", mode="before")
    @classmethod
    def normalize_webfig_base_url(cls, value: str | None) -> str:
        if not value:
            return ""
        return str(value).rstrip("/")

    @field_validator("app_public_url", mode="before")
    @classmethod
    def normalize_app_public_url(cls, value: str | None) -> str:
        if not value:
            return ""
        return str(value).rstrip("/")

    @property
    def postgres_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def mikrotik_routers(self) -> list[MikroTikRouterConfig]:
        if self.mikrotik_routers_json:
            raw_data = json.loads(self.mikrotik_routers_json)
            if not isinstance(raw_data, list):
                raise ValueError("MIKROTIK_ROUTERS_JSON must be a JSON array")
            return [MikroTikRouterConfig.model_validate(item) for item in raw_data]

        if self.mikrotik_host and self.mikrotik_user and self.mikrotik_password:
            return [
                MikroTikRouterConfig(
                    name="default-router",
                    host=self.mikrotik_host,
                    port=self.mikrotik_port,
                    user=self.mikrotik_user,
                    password=self.mikrotik_password,
                    role="default",
                )
            ]
        return []

    @property
    def enabled_alert_codes_set(self) -> set[str]:
        raw_codes = {
            item.strip()
            for item in (self.diag_enabled_alert_codes or "").split(",")
            if item.strip()
        }
        if not raw_codes:
            return set(ALL_ALERT_CODES)
        return {code for code in raw_codes if code in ALL_ALERT_CODES}

    @property
    def telegram_alert_codes_set(self) -> set[str]:
        raw_codes = {
            item.strip()
            for item in (self.telegram_alert_codes or "").split(",")
            if item.strip()
        }
        if not raw_codes:
            return set()
        return {code for code in raw_codes if code in ALL_ALERT_CODES}

    @property
    def webfig_enabled(self) -> bool:
        return (
            bool(self.dashboard_feature_webfig_tab)
            and bool(self.webfig_base_url.strip())
            and bool(self.webfig_username.strip())
            and bool(self.webfig_password.strip())
        )

    @property
    def smartolt_proxy_enabled(self) -> bool:
        return bool(self.dashboard_feature_smartolt_tab) and bool(self.smartolt_base_url.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
