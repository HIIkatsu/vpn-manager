import logging
from typing import Any

import grpc

from app.core.config import settings

logger = logging.getLogger(__name__)


class XrayService:
    @staticmethod
    async def add_client(email: str, uuid: str) -> bool:
        target = f"{settings.XRAY_GRPC_HOST}:{settings.XRAY_GRPC_PORT}"

        try:
            async with grpc.aio.insecure_channel(target) as channel:
                from xray_api import command_pb2, command_pb2_grpc  # type: ignore

                stub = command_pb2_grpc.HandlerServiceStub(channel)
                request: Any = command_pb2.AlterInboundRequest(
                    tag="vless-in",
                    operation=command_pb2.AddUserOperation(
                        email=email,
                        account=command_pb2.Account(id=uuid),
                    ),
                )
                await stub.AlterInbound(request)
                return True
        except grpc.RpcError:
            logger.exception("Failed to add client to Xray via gRPC")
            return False
        except Exception:
            logger.exception("Unexpected error while adding client to Xray")
            return False

    @staticmethod
    def generate_vless_link(uuid: str) -> str:
        return (
            f"vless://{uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp&security=reality&encryption=none&sni=example.com&fp=chrome&pbk=placeholder#VPN"
        )
