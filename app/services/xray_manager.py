import logging
import grpc
import json
from uuid import UUID
from app.core.settings import settings
# Исправленные импорты: разбили склеенную строку
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2

logger = logging.getLogger(__name__)

def _build_vless_account_message(uuid: str, flow: str = 'xtls-rprx-vision', encryption: str = 'none') -> bytes:
    normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
    payload = b''
    uuid_bytes = normalized_uuid.encode('utf-8')
    payload += b'\x0A' + bytes([len(uuid_bytes)]) + uuid_bytes
    flow_bytes = flow.encode('utf-8')
    payload += b'\x12' + bytes([len(flow_bytes)]) + flow_bytes
    enc_bytes = encryption.encode('utf-8')
    payload += b'\x1A' + bytes([len(enc_bytes)]) + enc_bytes
    return payload

class XrayManager:
    def __init__(self):
        self.target = "127.0.0.1:8080"
        self.inbound_tag = "vless"
        try:
            with open("/usr/local/etc/xray/config.json", "r") as f:
                conf = json.load(f)
                for ib in conf.get("inbounds", []):
                    if ib.get("protocol") == "dokodemo-door":
                        self.target = f"127.0.0.1:{ib.get('port')}"
                    if ib.get("protocol") == "vless":
                        self.inbound_tag = ib.get("tag", "vless")
        except Exception:
            pass

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

    async def add_client(self, email: str, uuid: str) -> bool:
        try:
            account_bytes = _build_vless_account_message(uuid)
            typed_account = typed_message_pb2.TypedMessage(
                type="xray.proxy.vless.Account",
                value=account_bytes
            )
            user = user_pb2.User(email=email, account=typed_account)
            operation = command_pb2.AddUserOperation(user=user)
            op_typed = typed_message_pb2.TypedMessage(
                type="xray.app.proxyman.command.AddUserOperation",
                value=operation.SerializeToString()
            )
            request = command_pb2.AlterInboundRequest(tag=self.inbound_tag, operation=op_typed)
            
            async with grpc.aio.insecure_channel(self.target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                await stub.AlterInbound(request)
            return True
        except grpc.RpcError as e:
            # ТИХАЯ ОБРАБОТКА: если юзер уже есть — это успех
            if "already exists" in str(e.details()):
                return True
            logger.error(f"gRPC AddClient Error for {email}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error for {email}: {e}")
            return False
