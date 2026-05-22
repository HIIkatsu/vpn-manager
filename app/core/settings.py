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

    YOOKASSA_WEBHOOK_AUTH: str | None = None
    YOOKASSA_WEBHOOK_IP_ALLOWLIST: str = ""
    TRUSTED_PROXY_IPS: str = ""

    SUBSCRIPTION_RATE_LIMIT_PER_MINUTE: int = 5
    YOOKASSA_RATE_LIMIT_PER_MINUTE: int = 30

    XRAY_GRPC_HOST: str
    XRAY_GRPC_PORT: int
    WEBHOOK_URL_DOMAIN: str
    VLESS_SNI: str
    VLESS_PUBLIC_KEY: str
    VLESS_SHORT_ID: str
    VLESS_FINGERPRINT: str
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
