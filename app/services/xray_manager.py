import logging
import grpc
import json
import re
import asyncio
import urllib.parse
from uuid import UUID
from app.core.settings import settings
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
        self.inbound_tags = []
        try:
            with open("/usr/local/etc/xray/config.json", "r") as f:
                conf = json.load(f)
                for ib in conf.get("inbounds", []):
                    if ib.get("protocol") == "dokodemo-door":
                        self.target = f"127.0.0.1:{ib.get('port')}"
                    if ib.get("protocol") == "vless":
                        tag = ib.get("tag")
                        if tag:
                            self.inbound_tags.append(tag)
        except Exception:
            pass
        if not self.inbound_tags:
            self.inbound_tags = ["vless"]

    def generate_vless_subscription(self, uuid: str) -> str:
        normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid

        # ВСЕ ссылки теперь отдают клиенту 443 порт!
        fin_smart_base = (
            f"vless://{normalized_uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp&security=reality"
            f"&fp={settings.VLESS_FINGERPRINT}&pbk={settings.VLESS_PUBLIC_KEY}"
            f"&sni={settings.VLESS_SNI}&sid={settings.VLESS_SHORT_ID}"
            "&alpn=h2%2Chttp%2F1.1&flow=xtls-rprx-vision"
        )

        fin_usa_base = (
            f"vless://{normalized_uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp&security=reality"
            f"&fp={settings.VLESS_FINGERPRINT}&pbk={settings.VLESS_PUBLIC_KEY}"
            "&sni=www.microsoft.com"
            f"&sid={settings.VLESS_SHORT_ID}"
            "&alpn=h2%2Chttp%2F1.1&flow=xtls-rprx-vision"
        )

        fin_direct_base = (
            f"vless://{normalized_uuid}@{settings.WEBHOOK_URL_DOMAIN}:443"
            "?type=tcp&security=reality"
            f"&fp={settings.VLESS_FINGERPRINT}&pbk={settings.VLESS_PUBLIC_KEY}"
            "&sni=www.apple.com"
            f"&sid={settings.VLESS_SHORT_ID}"
            "&alpn=h2%2Chttp%2F1.1&flow=xtls-rprx-vision"
        )

        configs = [
            f"{fin_smart_base}#{urllib.parse.quote('🇪🇺 AUTO (Умный выбор)')}",
            f"{fin_smart_base}#{urllib.parse.quote('🇪🇺 YouTube (AUTO)')}",
            f"{fin_usa_base}#{urllib.parse.quote('🇺🇸 США (Чистый IP)')}",
            f"{fin_usa_base}#{urllib.parse.quote('🇺🇸 Gemini & ChatGPT (США)')}",
            f"{fin_direct_base}#{urllib.parse.quote('🇫🇮 Финляндия (Direct)')}",
            f"{fin_smart_base}#{urllib.parse.quote('🇪🇺 Instagram (AUTO)')}"
        ]
        return "\n".join(configs)

    async def add_client(self, email: str, uuid: str) -> bool:
        success_overall = True
        try:
            account_bytes = _build_vless_account_message(uuid)
            typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
            user = user_pb2.User(email=email, account=typed_account)
            operation = command_pb2.AddUserOperation(user=user)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=operation.SerializeToString())
            async with grpc.aio.insecure_channel(self.target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                for tag in self.inbound_tags:
                    request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                    try:
                        await stub.AlterInbound(request)
                    except grpc.RpcError as e:
                        if "already exists" not in str(e.details()):
                            success_overall = False
        except Exception:
            return False
        return success_overall

    async def remove_client(self, email: str) -> bool:
        success_overall = True
        try:
            operation = command_pb2.RemoveUserOperation(email=email)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=operation.SerializeToString())
            async with grpc.aio.insecure_channel(self.target) as channel:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                for tag in self.inbound_tags:
                    request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                    try:
                        await stub.AlterInbound(request)
                    except grpc.RpcError as e:
                        if "not found" not in str(e.details()).lower():
                            success_overall = False
        except Exception:
            return False
        return success_overall

    async def get_live_traffic_stats(self, reset: bool = False) -> dict[str, int]:
        try:
            cmd = ["xray", "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", "user>>>"]
            if reset:
                cmd.append("-reset")
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0: return {}
            data = json.loads(stdout.decode())
            stats_list = data.get("stat", []) or data.get("stats", [])
            traffic_map = {}
            for item in stats_list:
                name = item.get("name", "")
                value = int(item.get("value", 0))
                match = re.match(r"user>>>(?P<uuid>.+?)>>>traffic>>>(uplink|downlink)$", name)
                if match:
                    user_uuid = match.group("uuid")
                    traffic_map[user_uuid] = traffic_map.get(user_uuid, 0) + value
            return traffic_map
        except Exception:
            return {}
