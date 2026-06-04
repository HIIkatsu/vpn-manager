#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID

import grpc
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.grpc.xray_api.app.proxyman.command import command_pb2, command_pb2_grpc
from app.grpc.xray_api.common.protocol import user_pb2
from app.grpc.xray_api.common.serial import typed_message_pb2

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("xray-node-receiver")

XRAY_CONFIG_PATH = os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
XRAY_GRPC_TARGET = os.getenv("XRAY_GRPC_TARGET", "")
SYNC_NODES_TOKEN = os.getenv("SYNC_NODES_TOKEN", "")
SYNC_MAX_SKEW_SECONDS = int(os.getenv("SYNC_MAX_SKEW_SECONDS", "300"))
XRAY_REQUEST_TIMEOUT_SECONDS = float(os.getenv("XRAY_REQUEST_TIMEOUT_SECONDS", "5"))
XRAY_REQUEST_RETRIES = int(os.getenv("XRAY_REQUEST_RETRIES", "2"))


def _build_vless_account_message(uuid: str, flow: str = "xtls-rprx-vision", encryption: str = "none") -> bytes:
    normalized_uuid = str(UUID(uuid)) if len(uuid) == 32 else uuid
    payload = b""
    uuid_bytes = normalized_uuid.encode("utf-8")
    payload += b"\x0A" + bytes([len(uuid_bytes)]) + uuid_bytes
    if flow:
        flow_bytes = flow.encode("utf-8")
        payload += b"\x12" + bytes([len(flow_bytes)]) + flow_bytes
    enc_bytes = encryption.encode("utf-8")
    payload += b"\x1A" + bytes([len(enc_bytes)]) + enc_bytes
    return payload


def _load_xray_targets() -> tuple[str, list[str]]:
    target = XRAY_GRPC_TARGET or "127.0.0.1:10085"
    inbound_tags: list[str] = []
    with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        config = json.load(config_file)
    for inbound in config.get("inbounds", []):
        if inbound.get("protocol") == "dokodemo-door" and not XRAY_GRPC_TARGET:
            target = f"127.0.0.1:{inbound.get('port')}"
        if inbound.get("protocol") == "vless" and inbound.get("tag"):
            inbound_tags.append(str(inbound["tag"]))
    if not inbound_tags:
        raise RuntimeError(f"No VLESS inbound tags found in {XRAY_CONFIG_PATH}")
    return target, inbound_tags


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


class ClientUpdate(BaseModel):
    action: str = Field(pattern="^(add|remove)$")
    telegram_id: str
    uuid: str | None = None
    event_id: str | None = None


class XrayClientMutator:
    def __init__(self) -> None:
        self.target = ""
        self.inbound_tags: list[str] = []
        self.channel: grpc.aio.Channel | None = None

    async def start(self) -> None:
        self.target, self.inbound_tags = _load_xray_targets()
        self.channel = grpc.aio.insecure_channel(self.target)
        logger.info("Receiver connected to Xray gRPC target=%s inbounds=%s", self.target, ",".join(self.inbound_tags))

    async def stop(self) -> None:
        if self.channel is not None:
            await self.channel.close()

    async def add_client(self, email: str, uuid: str) -> bool:
        return await self._alter_all("add", email=email, uuid=uuid)

    async def remove_client(self, email: str) -> bool:
        return await self._alter_all("remove", email=email, uuid=None)

    async def _alter_all(self, action: str, *, email: str, uuid: str | None) -> bool:
        if self.channel is None:
            raise RuntimeError("Xray channel is not initialized")
        stub = command_pb2_grpc.HandlerServiceStub(self.channel)
        success_overall = True
        for tag in self.inbound_tags:
            if action == "add":
                flow = "xtls-rprx-vision" if "ws" not in tag.lower() else ""
                account = typed_message_pb2.TypedMessage(
                    type="xray.proxy.vless.Account",
                    value=_build_vless_account_message(str(uuid), flow=flow),
                )
                operation = command_pb2.AddUserOperation(user=user_pb2.User(email=email, account=account))
                op_type = "xray.app.proxyman.command.AddUserOperation"
                idempotent_error = "already exists"
            else:
                operation = command_pb2.RemoveUserOperation(email=email)
                op_type = "xray.app.proxyman.command.RemoveUserOperation"
                idempotent_error = "not found"

            request = command_pb2.AlterInboundRequest(
                tag=tag,
                operation=typed_message_pb2.TypedMessage(type=op_type, value=operation.SerializeToString()),
            )
            if not await self._call_with_retry(stub, request, idempotent_error):
                success_overall = False
        return success_overall

    async def _call_with_retry(self, stub, request, idempotent_error: str) -> bool:
        for attempt in range(XRAY_REQUEST_RETRIES + 1):
            try:
                await asyncio.wait_for(stub.AlterInbound(request), timeout=XRAY_REQUEST_TIMEOUT_SECONDS)
                return True
            except grpc.RpcError as exc:
                details = str(exc.details()).lower()
                if idempotent_error in details:
                    return True
                if attempt < XRAY_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2**attempt))
            except asyncio.TimeoutError:
                if attempt < XRAY_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2**attempt))
        return False


mutator = XrayClientMutator()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not SYNC_NODES_TOKEN:
        raise RuntimeError("SYNC_NODES_TOKEN is required")
    await mutator.start()
    yield
    await mutator.stop()


app = FastAPI(title="Xray Active Push Receiver", lifespan=lifespan)


async def _verify_request(request: Request, authorization: str, timestamp: str, signature: str) -> bytes:
    if not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization.removeprefix("Bearer "), SYNC_NODES_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid timestamp") from exc
    if abs(int(time.time()) - request_time) > SYNC_MAX_SKEW_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="stale timestamp")
    body = await request.body()
    expected = _signature(SYNC_NODES_TOKEN, timestamp, body)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    return body


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/xray/client")
async def apply_client_update(
    request: Request,
    authorization: str = Header(alias="Authorization"),
    timestamp: str = Header(alias="X-Sync-Timestamp"),
    signature: str = Header(alias="X-Sync-Signature"),
) -> dict[str, str]:
    body = await _verify_request(request, authorization, timestamp, signature)
    update = ClientUpdate.model_validate_json(body)
    if update.action == "add":
        if not update.uuid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="uuid is required for add")
        ok = await mutator.add_client(email=update.telegram_id, uuid=update.uuid)
    else:
        ok = await mutator.remove_client(email=update.telegram_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="xray mutation failed")
    return {"status": "ok", "action": update.action, "telegram_id": update.telegram_id}
