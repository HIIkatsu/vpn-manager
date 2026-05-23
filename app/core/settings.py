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

    YOOKASSA_WEBHOOK_SHARED_TOKEN: str | None = None
    YOOKASSA_WEBHOOK_REQUIRE_API_VERIFY: bool = True
    YOOKASSA_WEBHOOK_IP_ALLOWLIST: str = ""
    TRUSTED_PROXY_IPS: str = ""

    SUBSCRIPTION_RATE_LIMIT_PER_MINUTE: int = 5
    YOOKASSA_RATE_LIMIT_PER_MINUTE: int = 30
    BILLING_PENDING_BATCH_SIZE: int = 50
    YOOKASSA_REQUEST_TIMEOUT_SECONDS: float = 10.0
    YOOKASSA_REQUEST_RETRIES: int = 2
    XRAY_REQUEST_TIMEOUT_SECONDS: float = 5.0
    XRAY_REQUEST_RETRIES: int = 2
    BILLING_PROCESSING_STALE_AFTER_SECONDS: int = 300

    XRAY_GRPC_HOST: str
    XRAY_GRPC_PORT: int
    WEBHOOK_URL_DOMAIN: str
    VLESS_SNI: str
    VLESS_PUBLIC_KEY: str
    VLESS_SHORT_ID: str
    VLESS_FINGERPRINT: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
