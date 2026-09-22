from pydantic_settings import BaseSettings, SettingsConfigDict


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
