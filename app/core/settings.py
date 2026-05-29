from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DEBUG: bool = False
    SECRET_PREFIX: str

    DATABASE_URL: str

    BOT_TOKEN: str
    WEBHOOK_URL: str
    WEBHOOK_SECRET: str | None = None

    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str

    YOOKASSA_WEBHOOK_AUTH: str | None = None
    YOOKASSA_WEBHOOK_SECRET: str | None = None
    YOOKASSA_WEBHOOK_REQUIRE_API_VERIFY: bool = True
    YOOKASSA_WEBHOOK_IP_ALLOWLIST: str = ""
    TRUSTED_PROXY_IPS: str = ""

    SUBSCRIPTION_RATE_LIMIT_PER_MINUTE: int = 5
    YOOKASSA_RATE_LIMIT_PER_MINUTE: int = 30
    REDIS_URL: str | None = None
    WEBHOOK_REPLAY_TTL_SECONDS: int = 3600
    RATE_LIMIT_FAIL_OPEN: bool = False
    BILLING_PENDING_BATCH_SIZE: int = 50
    YOOKASSA_REQUEST_TIMEOUT_SECONDS: float = 10.0
    YOOKASSA_REQUEST_RETRIES: int = 2
    XRAY_REQUEST_TIMEOUT_SECONDS: float = 5.0
    XRAY_REQUEST_RETRIES: int = 2
    BILLING_PROCESSING_STALE_AFTER_SECONDS: int = 300

    SYNC_NODES_TOKEN: str
    SYNC_NODES_IP_ALLOWLIST: str = "132.243.194.119/32,194.50.94.177/32,150.251.152.174/32"

    FINLAND_PUBLIC_IP: str = "150.251.152.174"
    GERMANY_PUBLIC_IP: str = "132.243.194.119"
    NETHERLANDS_PUBLIC_IP: str = "194.50.94.177"
    RUSSIA_BALANCER_IP: str = "132.243.230.173"
    XRAY_MAIN_PORT: int = 443
    XRAY_REDIRECT_PORT: int = 20443
    XRAY_RU_CLEAN_PORT: int = 10444
    XRAY_RU_WHITELIST_PORT: int = 10445
    XRAY_RU_CLEAN_SHORT_ID: str = "45b6b57266629593"
    XRAY_RU_WHITELIST_SHORT_ID: str = "45b6b57266629594"

    XRAY_GRPC_HOST: str
    XRAY_GRPC_PORT: int
    WEBHOOK_URL_DOMAIN: str
    VLESS_SNI: str
    VLESS_PUBLIC_KEY: str
    XRAY_REALITY_PUBLIC_KEY: str | None = None
    XRAY_REALITY_PRIVATE_KEY: str
    VLESS_SHORT_ID: str
    VLESS_FINGERPRINT: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
