import logging
from typing import Any
from uuid import UUID

import grpc

from app.core.settings import settings
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2

logger = logging.getLogger(__name__)


class XrayManager:
    async def add_client(self, email: str, uuid: str) -> bool:
        target = f"{settings.XRAY_GRPC_HOST}:{settings.XRAY_GRPC_PORT}"
        try:
            async with grpc.aio.insecure_channel(target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                add_user = command_pb2.AddUserOperation(
                    user=user_pb2.User(
                        email=email,
                        account=typed_message_pb2.TypedMessage(),
                    )
                )
                request: Any = command_pb2.AlterInboundRequest(
                    tag="vless-reality",
                    operation=typed_message_pb2.TypedMessage(
                        type="xray.app.proxyman.command.AddUserOperation",
                        value=add_user.SerializeToString(),
                    ),
                )
                await stub.AlterInbound(request)
                return True
        except grpc.RpcError as exc:
            details = (exc.details() or "").lower() if hasattr(exc, "details") else ""
            if "already" in details and "exist" in details:
                logger.warning("Client already exists in Xray, treat as success", extra={"email": email})
                return True
            logger.exception("Failed to add client to Xray via gRPC")
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
            f"&sid={settings.VLESS_SHORT_ID}#VPN"
        )
