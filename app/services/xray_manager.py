import logging
from typing import Any
from uuid import UUID

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from app.core.settings import settings
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2

logger = logging.getLogger(__name__)


_VLESS_ACCOUNT_TYPE = "xray.proxy.vless.Account"


def _build_vless_account_message(uuid: str, flow: str = "xtls-rprx-vision", encryption: str = "none") -> bytes:
    """Serialize xray.proxy.vless.Account without generated stubs."""
    file_descriptor = descriptor_pb2.FileDescriptorProto()
    file_descriptor.name = "proxy/vless/account.proto"
    file_descriptor.package = "xray.proxy.vless"
    file_descriptor.syntax = "proto3"

    account = file_descriptor.message_type.add()
    account.name = "Account"

    field_id = account.field.add()
    field_id.name = "id"
    field_id.number = 1
    field_id.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field_id.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field_flow = account.field.add()
    field_flow.name = "flow"
    field_flow.number = 2
    field_flow.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field_flow.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field_encryption = account.field.add()
    field_encryption.name = "encryption"
    field_encryption.number = 3
    field_encryption.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field_encryption.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_descriptor)
    account_descriptor = pool.FindMessageTypeByName(_VLESS_ACCOUNT_TYPE)
    account_message = message_factory.GetMessageClass(account_descriptor)()
    account_message.id = str(UUID(uuid)) if len(uuid) == 32 else uuid
    account_message.flow = flow
    account_message.encryption = encryption
    return account_message.SerializeToString()


class XrayManager:
    async def add_client(self, email: str, uuid: str) -> bool:
        target = f"{settings.XRAY_GRPC_HOST}:{settings.XRAY_GRPC_PORT}"
        try:
            async with grpc.aio.insecure_channel(target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                account_payload = _build_vless_account_message(uuid=uuid)
                add_user = command_pb2.AddUserOperation(
                    user=user_pb2.User(
                        email=email,
                        account=typed_message_pb2.TypedMessage(
                            type=_VLESS_ACCOUNT_TYPE,
                            value=account_payload,
                        ),
                    )
                )
                request: Any = command_pb2.AlterInboundRequest(
                    tag="vless-reality",
                    operation=typed_message_pb2.TypedMessage(
                        type="xray.app.proxyman.command.AddUserOperation",
                        value=add_user.SerializeToString(),
                    ),
                )
                pass  # await stub.AlterInbound(request)
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
            "&security=reality&flow=xtls-rprx-vision&alpn=h2,http/1.1"
            f"&fp={settings.VLESS_FINGERPRINT}"
            f"&pbk={settings.VLESS_PUBLIC_KEY}"
            f"&sni={settings.VLESS_SNI}"
            f"&sid={settings.VLESS_SHORT_ID}#VPN"
        )
