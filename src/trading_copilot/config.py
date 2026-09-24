from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


class Settings(BaseSettings):
    app_env: str = "dev"
    bybit_base_url: str = "https://api.bybit.com"
    bybit_ws_linear_url: str = "wss://stream.bybit.com/v5/public/linear"
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    bybit_read_only_sync_enabled: bool = False
    live_stream_enabled: bool = False
    live_stream_symbols: str = "BTCUSDT,ETHUSDT,BNBUSDT"
    default_account_equity: float = 5000.0
    default_max_portfolio_risk_pct: float = 0.02
    database_url: str = "sqlite+aiosqlite:///./trading_copilot.db"
    state_change_monitor_enabled: bool = False
    state_change_monitor_interval_seconds: float = 5.0
    setup_scanner_enabled: bool = False
    setup_scanner_interval_seconds: float = Field(default=30.0, gt=0)
    setup_scanner_concurrency: int = Field(default=4, ge=1)
    trigger_monitor_enabled: bool = False
    trigger_monitor_interval_seconds: float = Field(default=15.0, gt=0)
    trigger_monitor_concurrency: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def validate_database(self):
        self.database_url = normalize_database_url(self.database_url)
        if self.app_env.lower() == "prod" and not self.database_url.startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError("APP_ENV=prod requires a PostgreSQL DATABASE_URL")
        if self.trigger_monitor_enabled and not self.setup_scanner_enabled:
            raise ValueError("TRIGGER_MONITOR_ENABLED requires SETUP_SCANNER_ENABLED")
        return self

    @property
    def stream_symbols(self) -> list[str]:
        return [
            symbol.strip().upper()
            for symbol in self.live_stream_symbols.split(",")
            if symbol.strip()
        ]

    @property
    def has_read_only_credentials(self) -> bool:
        return bool(self.bybit_api_key and self.bybit_api_secret)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
