from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    bybit_base_url: str = "https://api.bybit.com"
    bybit_ws_linear_url: str = "wss://stream.bybit.com/v5/public/linear"
    live_stream_enabled: bool = False
    live_stream_symbols: str = "BTCUSDT,ETHUSDT,BNBUSDT"
    default_account_equity: float = 5000.0
    default_max_portfolio_risk_pct: float = 0.02

    @property
    def stream_symbols(self) -> list[str]:
        return [
            symbol.strip().upper()
            for symbol in self.live_stream_symbols.split(",")
            if symbol.strip()
        ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
