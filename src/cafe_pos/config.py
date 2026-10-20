import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


APP_ENV = os.getenv("APP_ENV", "sandbox")


class Settings(BaseSettings):
    app_env: str = APP_ENV
    debug: bool = False
    app_name: str = "Cloud POS для мережі кав'ярень DEV"

    # Ці два поля без значень за замовчуванням: без них застосунок не стартує.
    database_url: str
    secret_key: str

    # Пул підключень до бази (на одну репліку). Максимум підключень = size + overflow.
    # Для трьох реплік це має вміщатися в max_connections PostgreSQL.
    db_pool_size: int = 20
    db_max_overflow: int = 20
    db_pool_timeout: int = 10

    # Кеш Redis. Порожнє значення вимикає кеш.
    redis_url: Optional[str] = None
    cache_ttl_seconds: int = 30
    cache_ttl_summary_seconds: int = 10

    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / f".env.{APP_ENV}",
        extra="ignore",
    )


settings = Settings()
