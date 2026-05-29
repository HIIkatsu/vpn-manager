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
# Новые импорты для работы со статистикой через gRPC
from app.grpc.xray_api.app.stats.command import command_pb2 as stats_pb2
from app.grpc.xray_api.app.stats.command import command_pb2_grpc as stats_pb2_grpc

logger = logging.getLogger(__name__)

def _build_vless_account_message(uuid: str, flow: str = 'xtls-rprx-vision', encryption: str = 'none') -> bytes:
    normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
    payload = b''
    uuid_bytes = normalized_uuid.encode('utf-8')
    payload += b'\x0A' + bytes([len(uuid_bytes)]) + uuid_bytes
    
    # Если flow пустой (например, для WebSocket), не добавляем этот байт в protobuf
    if flow:
        flow_bytes = flow.encode('utf-8')
        payload += b'\x12' + bytes([len(flow_bytes)]) + flow_bytes
        
    enc_bytes = encryption.encode('utf-8')
    payload += b'\x1A' + bytes([len(enc_bytes)]) + enc_bytes
    return payload

class XrayManager:
    _channel = None
    _inbound_tags = None
    _target = "127.0.0.1:10085"
    _config_lock = asyncio.Lock()

    @classmethod
    async def _init_config(cls):
        if cls._inbound_tags is not None:
            return
        async with cls._config_lock:
            if cls._inbound_tags is not None:
                return
            conf = {}
            try:
                conf = await asyncio.to_thread(cls._read_xray_config)
            except Exception:
                logger.exception("Failed to load xray config, using safe defaults")
            cls._inbound_tags = []
            for ib in conf.get("inbounds", []):
                if ib.get("protocol") == "dokodemo-door":
                    cls._target = f"127.0.0.1:{ib.get('port')}"
                if ib.get("protocol") == "vless":
                    tag = ib.get("tag")
                    if tag:
                        cls._inbound_tags.append(tag)
            if not cls._inbound_tags:
                cls._inbound_tags = ["vless-smart", "vless-ws"]

    @staticmethod
    def _read_xray_config() -> dict:
        with open("/usr/local/etc/xray/config.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    async def get_channel(cls):
        await cls._init_config()
        if cls._channel is None:
            cls._channel = grpc.aio.insecure_channel(cls._target)
        return cls._channel

    async def initialize(self):
        await self._init_config()
        self.target = self._target
        self.inbound_tags = self._inbound_tags

    @classmethod
    async def close_channel(cls):
        if cls._channel is not None:
            await cls._channel.close()
            cls._channel = None

    async def add_client(self, email: str, uuid: str) -> bool:
        success_overall = True
        try:
            await self.initialize()
            channel = await self.get_channel()
            stub = command_pb2_grpc.HandlerServiceStub(channel)
            
            for tag in self.inbound_tags:
                # УМНЫЙ ВЫБОР FLOW: Отключаем Vision для WebSocket (тег содержит ws)
                flow = "xtls-rprx-vision" if "ws" not in tag.lower() else ""
                
                account_bytes = _build_vless_account_message(uuid, flow=flow)
                typed_account = typed_message_pb2.TypedMessage(type="xray.proxy.vless.Account", value=account_bytes)
                user = user_pb2.User(email=email, account=typed_account)
                operation = command_pb2.AddUserOperation(user=user)
                op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.AddUserOperation", value=operation.SerializeToString())
                
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
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                    except asyncio.TimeoutError:
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                if not done:
                    success_overall = False
        except Exception as e:
            logger.exception("Xray add_client failed", extra=log_context(telegram_id=email, action_source="xray_add_client"))
            return False
        return success_overall

    async def remove_client(self, email: str) -> bool:
        success_overall = True
        try:
            await self.initialize()
            operation = command_pb2.RemoveUserOperation(email=email)
            op_typed = typed_message_pb2.TypedMessage(type="xray.app.proxyman.command.RemoveUserOperation", value=operation.SerializeToString())
            
            channel = await self.get_channel()
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
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                    except asyncio.TimeoutError:
                        if attempt < settings.XRAY_REQUEST_RETRIES:
                            await asyncio.sleep(0.2 * (2 ** attempt))
                if not done:
                    success_overall = False
        except Exception as e:
            logger.exception("Xray remove_client failed")
            return False
        return success_overall

    async def get_live_traffic_stats(self, reset: bool = False) -> dict[str, int]:
        # ПОЛНОСТЬЮ ИЗБАВИЛИСЬ ОТ SUBPROCESS
        traffic_map = {}
        try:
            channel = await self.get_channel()
            stub = stats_pb2_grpc.StatsServiceStub(channel)
            request = stats_pb2.QueryStatsRequest(pattern="user>>>", reset=reset)
            
            # gRPC вызов напрямую в память Xray
            response = await asyncio.wait_for(stub.QueryStats(request), timeout=5.0)
            
            for stat in response.stat:
                name = stat.name
                value = stat.value
                match = re.match(r"user>>>(?P<uuid>.+?)>>>traffic>>>(uplink|downlink)$", name)
                if match:
                    user_uuid = match.group("uuid")
                    traffic_map[user_uuid] = traffic_map.get(user_uuid, 0) + value
        except Exception as e:
            logger.warning(f"Failed to get stats via gRPC: {e}")
        return traffic_map