from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DEBUG: bool = False
    SECRET_PREFIX: str

    DATABASE_URL: str

    BOT_TOKEN: str
    WEBHOOK_URL: str
    WEBHOOK_SECRET: str | None = None

    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str

    XRAY_GRPC_HOST: str
    XRAY_GRPC_PORT: int


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
