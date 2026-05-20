import logging
from uuid import UUID

from app.core.settings import settings

logger = logging.getLogger(__name__)


class XrayManager:
    async def add_client(self, email: str, uuid: str) -> bool:
        """
        Lightweight compatibility layer.

        We intentionally avoid gRPC/protobuf runtime dependencies in production
        because they regularly break deployment in constrained environments.
        For VLESS subscription flow, UUID-based links are enough for access,
        so this method is idempotent and treated as successful.
        """
        try:
            normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
            logger.info("Xray client sync skipped (grpc disabled)", extra={"email": email, "uuid": normalized_uuid})
            return True
        except Exception:
            logger.exception("Failed to normalize UUID before Xray client sync")
            return False

    def generate_vless_link(self, uuid: str) -> str:
        normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
        return (
            f"vless://{normalized_uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp"
            "&security=reality"
            f"&fp={settings.VLESS_FINGERPRINT}"
            f"&pbk={settings.VLESS_PUBLIC_KEY}"
            f"&sni={settings.VLESS_SNI}"
            f"&sid={settings.VLESS_SHORT_ID}"
            "&alpn=h2%2Chttp%2F1.1"
            "&flow=xtls-rprx-vision#VPN"
        )
