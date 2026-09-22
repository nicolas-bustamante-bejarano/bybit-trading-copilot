from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    bybit_base_url: str = "https://api.bybit.com"
    default_account_equity: float = 5000.0
    default_max_portfolio_risk_pct: float = 0.02

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
