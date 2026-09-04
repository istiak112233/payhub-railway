from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    admin_password: str = "admin123"
    admin_email: str = "admin@gmail.com"
    public_base_url: str = "http://127.0.0.1:8000"
    admin_bot_token: str = ""
    admin_telegram_id: str = ""
    default_coin: str = "USDT"
    invoice_expire_minutes: int = 30
    session_secret: str = "payhub-secret-change-me"
    database_url: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 10
    # Optional global fallback only. Normal verification uses each approved user's saved keys.
    binance_api_key: str = ""
    binance_api_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
