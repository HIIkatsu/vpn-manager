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
    _channel = None
    _inbound_tags = None
    _target = "127.0.0.1:8080"

    @classmethod
    def _init_config(cls):
        if cls._inbound_tags is not None:
            return
        cls._inbound_tags = []
        try:
            with open("/usr/local/etc/xray/config.json", "r") as f:
                conf = json.load(f)
                for ib in conf.get("inbounds", []):
                    if ib.get("protocol") == "dokodemo-door":
                        cls._target = f"127.0.0.1:{ib.get('port')}"
                    if ib.get("protocol") == "vless":
                        tag = ib.get("tag")
                        if tag:
                            cls._inbound_tags.append(tag)
        except Exception:
            pass
        if not cls._inbound_tags:
            cls._inbound_tags = ["vless"]

    @classmethod
    def get_channel(cls):
        cls._init_config()
        if cls._channel is None:
            cls._channel = grpc.aio.insecure_channel(cls._target)
        return cls._channel

    def __init__(self):
        self._init_config()
        self.target = self._target
        self.inbound_tags = self._inbound_tags

    async def add_client(self, email: str, uuid: str) -> bool:
        success_overall = True
        try:
            account_bytes = _build_vless_account_message(uuid)
            typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
            user = user_pb2.User(email=email, account=typed_account)
            operation = command_pb2.AddUserOperation(user=user)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=operation.SerializeToString())
            
            channel = self.get_channel()
            stub = command_pb2_grpc.HandlerServiceStub(channel)
            for tag in self.inbound_tags:
                request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                done = False
                for attempt in range(settings.XRAY_REQUEST_RETRIES + 1):
                    try:
                        await asyncio.wait_for(stub.AlterInbound(request), timeout=settings.XRAY_REQUEST_TIMEOUT_SECONDS)
                        done = True
                        break
                    except grpc.RpcError as e:
                        if "already exists" in str(e.details()):
                            done = True
                            break
                        logger.warning("Xray add_client rpc failed", extra={"email": email, "tag": tag, "attempt": attempt + 1})
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                    except asyncio.TimeoutError:
                        logger.warning("Xray add_client rpc timeout", extra={"email": email, "tag": tag, "attempt": attempt + 1})
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                if not done:
                    success_overall = False
        except Exception as e:
            logger.error(f"Xray add_client failed: {e}")
            return False
        return success_overall

    async def remove_client(self, email: str) -> bool:
        success_overall = True
        try:
            operation = command_pb2.RemoveUserOperation(email=email)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=operation.SerializeToString())
            
            channel = self.get_channel()
            stub = command_pb2_grpc.HandlerServiceStub(channel)
            for tag in self.inbound_tags:
                request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                done = False
                for attempt in range(settings.XRAY_REQUEST_RETRIES + 1):
                    try:
                        await asyncio.wait_for(stub.AlterInbound(request), timeout=settings.XRAY_REQUEST_TIMEOUT_SECONDS)
                        done = True
                        break
                    except grpc.RpcError as e:
                        if "not found" in str(e.details()).lower():
                            done = True
                            break
                        logger.warning("Xray remove_client rpc failed", extra={"email": email, "tag": tag, "attempt": attempt + 1})
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                    except asyncio.TimeoutError:
                        logger.warning("Xray remove_client rpc timeout", extra={"email": email, "tag": tag, "attempt": attempt + 1})
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                if not done:
                    success_overall = False
        except Exception as e:
            logger.error(f"Xray remove_client failed: {e}")
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
