import logging
import grpc
import json
import re
import asyncio
from uuid import UUID
from app.core.settings import settings
from app.core.logging_utils import log_context
from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2
from app.grpc.xray_api.app.stats.command import command_pb2 as stats_pb2
from app.grpc.xray_api.app.stats.command import command_pb2_grpc as stats_pb2_grpc

logger = logging.getLogger(__name__)

def _build_vless_account_message(uuid: str, flow: str = 'xtls-rprx-vision', encryption: str = 'none') -> bytes:
    normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
    payload = b''
    uuid_bytes = normalized_uuid.encode('utf-8')
    payload += b'\x0A' + bytes([len(uuid_bytes)]) + uuid_bytes
    if flow:
        flow_bytes = flow.encode('utf-8')
        payload += b'\x12' + bytes([len(flow_bytes)]) + flow_bytes
    enc_bytes = encryption.encode('utf-8')
    payload += b'\x1A' + bytes([len(enc_bytes)]) + enc_bytes
    return payload

class XrayManager:
    def __init__(self): pass

    # --- ТЕ САМЫЕ ЗАГЛУШКИ ДЛЯ СТАРТА FASTAPI ---
    async def initialize(self): pass
    @classmethod
    async def get_channel(cls): pass
    @classmethod
    async def close_channel(cls): pass
    # ---------------------------------------------

    async def _get_current_config(self):
        try:
            conf = await asyncio.to_thread(self._read_json)
        except Exception as e:
            return "127.0.0.1:10085", ["vless-smart", "vless-euro2", "vless-ru-clean", "vless-ru-whitelist", "vless-warp-se", "vless-warp-yt", "vless-warp-gpt"]
        target = "127.0.0.1:10085"
        tags = []
        for ib in conf.get("inbounds", []):
            if ib.get("protocol") == "dokodemo-door":
                target = f"127.0.0.1:{ib.get('port')}"
            if ib.get("protocol") == "vless" and ib.get("tag"):
                tags.append(ib.get("tag"))
        return target, tags or ["vless-smart"]

    def _read_json(self) -> dict:
        with open("/usr/local/etc/xray/config.json", "r", encoding="utf-8") as f:
            return json.load(f)

    async def add_client(self, email: str, uuid: str) -> bool:
        success_overall = True
        try:
            target, tags = await self._get_current_config()
            channel = grpc.aio.insecure_channel(target)
            try:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                for tag in tags:
                    if "transit" in tag.lower():
                        continue
                    flow = "xtls-rprx-vision" if "ws" not in tag.lower() else ""
                    account_bytes = _build_vless_account_message(uuid, flow=flow)
                    typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
                    user = user_pb2.User(email=email, account=typed_account)
                    op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=command_pb2.AddUserOperation(user=user).SerializeToString())
                    request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                    
                    done = False
                    for attempt in range(3):
                        try:
                            await stub.AlterInbound(request, timeout=2.0)
                            done = True
                            break
                        except grpc.RpcError as e:
                            if "already exists" in str(e.details()).lower():
                                # Принудительное удаление
                                rm_op = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=command_pb2.RemoveUserOperation(email=email).SerializeToString())
                                rm_req = command_pb2.AlterInboundRequest(tag=tag, operation=rm_op)
                                try:
                                    await stub.AlterInbound(rm_req, timeout=2.0)
                                except Exception:
                                    pass
                            await asyncio.sleep(0.5)
                    if not done: success_overall = False
            finally:
                await channel.close()
        except Exception as e: open('/root/xray_grpc_errors.txt', 'a').write(f'Exception in add_client: {e}\n'); return False
        return success_overall

    async def remove_client(self, email: str) -> bool:
        success_overall = True
        try:
            target, tags = await self._get_current_config()
            channel = grpc.aio.insecure_channel(target)
            try:
                stub = command_pb2_grpc.HandlerServiceStub(channel)
                op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=command_pb2.RemoveUserOperation(email=email).SerializeToString())
                for tag in tags:
                    if "transit" in tag.lower():
                        continue
                    request = command_pb2.AlterInboundRequest(tag=tag, operation=op_typed)
                    done = False
                    for _ in range(2):
                        try:
                            await stub.AlterInbound(request, timeout=2.0)
                            done = True
                            break
                        except grpc.RpcError as e:
                            if "not found" in str(e.details()).lower():
                                done = True
                                break
                            await asyncio.sleep(0.5)
                    if not done: success_overall = False
            finally:
                await channel.close()
        except Exception as e: open('/root/xray_grpc_errors.txt', 'a').write(f'Exception in add_client: {e}\n'); return False
        return success_overall

    async def get_live_traffic_stats(self, reset: bool = False) -> dict[str, int]:
        traffic_map = {}
        try:
            target, _ = await self._get_current_config()
            channel = grpc.aio.insecure_channel(target)
            try:
                stub = stats_pb2_grpc.StatsServiceStub(channel)
                response = await stub.QueryStats(stats_pb2.QueryStatsRequest(pattern="user>>>", reset=reset), timeout=3.0)
                for stat in response.stat:
                    match = re.match(r"user>>>(?P<uuid>.+?)>>>traffic>>>(uplink|downlink)$", stat.name)
                    if match:
                        user_uuid = match.group("uuid")
                        traffic_map[user_uuid] = traffic_map.get(user_uuid, 0) + stat.value
            finally:
                await channel.close()
        except Exception: pass
        return traffic_map
