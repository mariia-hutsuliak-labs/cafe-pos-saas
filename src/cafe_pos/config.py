import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


APP_ENV = os.getenv("APP_ENV", "sandbox")


class Settings(BaseSettings):
    app_env: str = APP_ENV
    debug: bool = False
    app_name: str = "Cloud POS для мережі кав'ярень"

    # Ці два поля без значень за замовчуванням: без них застосунок не стартує.
    database_url: str
    secret_key: str

    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / f".env.{APP_ENV}",
        extra="ignore",
    )


settings = Settings()
