#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
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
XRAY_MUTATION_MODE = os.getenv("XRAY_MUTATION_MODE", "auto").lower()
XRAY_BINARY = os.getenv("XRAY_BINARY", "/usr/local/bin/xray")
XRAY_RELOAD_COMMAND = os.getenv("XRAY_RELOAD_COMMAND", "systemctl reload xray")


class MutationError(RuntimeError):
    pass


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


def _client_payload(email: str, uuid: str, tag: str) -> dict[str, str]:
    client = {"id": str(UUID(uuid)) if len(uuid) == 32 else uuid, "email": email}
    if "ws" not in tag.lower():
        client["flow"] = "xtls-rprx-vision"
    return client


def _load_xray_config() -> dict:
    with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def _load_xray_targets() -> tuple[str, list[str]]:
    target = XRAY_GRPC_TARGET or "127.0.0.1:10085"
    inbound_tags: list[str] = []
    config = _load_xray_config()
    for inbound in config.get("inbounds", []):
        if inbound.get("protocol") in {"dokodemo-door", "tunnel"} and inbound.get("tag") == config.get("api", {}).get("tag") and not XRAY_GRPC_TARGET:
            target = f"127.0.0.1:{inbound.get('port')}"
        if inbound.get("protocol") == "vless" and inbound.get("tag"):
            inbound_tags.append(str(inbound["tag"]))
    if not inbound_tags:
        raise RuntimeError(f"No VLESS inbound tags found in {XRAY_CONFIG_PATH}")
    return target, inbound_tags


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


class ClientUpdate(BaseModel):
    action: str = Field(pattern="^(add|remove|update)$")
    telegram_id: str
    uuid: str | None = None
    event_id: str | None = None


class XrayConfigMutator:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()

    async def add_client(self, email: str, uuid: str) -> bool:
        return await self._mutate("add", email=email, uuid=uuid)

    async def remove_client(self, email: str) -> bool:
        return await self._mutate("remove", email=email, uuid=None)

    async def update_client(self, email: str, uuid: str) -> bool:
        return await self._mutate("update", email=email, uuid=uuid)

    async def _mutate(self, action: str, *, email: str, uuid: str | None) -> bool:
        async with self.lock:
            config = _load_xray_config()
            changed = False
            for inbound in config.get("inbounds", []):
                if inbound.get("protocol") != "vless":
                    continue
                tag = str(inbound.get("tag") or "")
                settings = inbound.setdefault("settings", {})
                clients = settings.setdefault("clients", [])
                existing_idx = next((idx for idx, client in enumerate(clients) if str(client.get("email")) == email), None)
                if action == "add" or action == "update":
                    if uuid is None:
                        raise MutationError(f"uuid is required for {action}")
                    new_client = _client_payload(email, uuid, tag)
                    if existing_idx is None:
                        clients.append(new_client)
                        changed = True
                    elif clients[existing_idx].get("id") != new_client["id"]:
                        clients[existing_idx].update(new_client)
                        changed = True
                elif existing_idx is not None:
                    clients.pop(existing_idx)
                    changed = True
            if not changed:
                return True
            await self._validate_and_install(config)
            return True

    async def _validate_and_install(self, config: dict) -> None:
        config_path = Path(XRAY_CONFIG_PATH)
        config_dir = config_path.parent
        backup_path = config_path.with_suffix(config_path.suffix + f".bak.{int(time.time())}")
        fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_dir)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(config, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
            await self._run_command([XRAY_BINARY, "run", "-test", "-config", str(temp_path)], "xray config validation")
            config_path.replace(backup_path)
            temp_path.replace(config_path)
            try:
                await self._reload_xray()
            except Exception:
                config_path.replace(temp_path)
                backup_path.replace(config_path)
                await self._reload_xray()
                raise
        finally:
            if temp_path.exists():
                temp_path.unlink()

    async def _reload_xray(self) -> None:
        if not XRAY_RELOAD_COMMAND.strip():
            return
        await self._run_command(shlex.split(XRAY_RELOAD_COMMAND), "xray reload")

    async def _run_command(self, command: list[str], description: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MutationError(f"{description} failed to start: {exc}") from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            output = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise MutationError(f"{description} failed with code {process.returncode}: {output}")


class XrayGrpcMutator:
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

    async def update_client(self, email: str, uuid: str) -> bool:
        await self._alter_all("remove", email=email, uuid=None)
        return await self._alter_all("add", email=email, uuid=uuid)

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
                logger.warning("Xray gRPC AlterInbound failed on attempt %s/%s: %s", attempt + 1, XRAY_REQUEST_RETRIES + 1, exc.details())
                if idempotent_error in details:
                    return True
                if attempt < XRAY_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2**attempt))
            except asyncio.TimeoutError:
                logger.warning("Xray gRPC AlterInbound timed out on attempt %s/%s", attempt + 1, XRAY_REQUEST_RETRIES + 1)
                if attempt < XRAY_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2**attempt))
        return False


class XrayClientMutator:
    def __init__(self) -> None:
        self.grpc = XrayGrpcMutator()
        self.config = XrayConfigMutator()
        if XRAY_MUTATION_MODE not in {"auto", "grpc", "config"}:
            raise RuntimeError("XRAY_MUTATION_MODE must be one of: auto, grpc, config")

    async def start(self) -> None:
        if XRAY_MUTATION_MODE in {"auto", "grpc"}:
            await self.grpc.start()
        else:
            _load_xray_targets()
            logger.info("Receiver will mutate %s directly and run reload command: %s", XRAY_CONFIG_PATH, XRAY_RELOAD_COMMAND)

    async def stop(self) -> None:
        await self.grpc.stop()

    async def add_client(self, email: str, uuid: str) -> bool:
        return await self._mutate("add", email=email, uuid=uuid)

    async def remove_client(self, email: str) -> bool:
        return await self._mutate("remove", email=email, uuid=None)

    async def update_client(self, email: str, uuid: str) -> bool:
        return await self._mutate("update", email=email, uuid=uuid)

    async def _mutate(self, action: str, *, email: str, uuid: str | None) -> bool:
        if XRAY_MUTATION_MODE in {"auto", "grpc"}:
            try:
                if action == "add":
                    ok = await self.grpc.add_client(email, str(uuid))
                elif action == "update":
                    ok = await self.grpc.update_client(email, str(uuid))
                else:
                    ok = await self.grpc.remove_client(email)
            except Exception as exc:
                logger.warning("Xray gRPC mutation raised an exception: %s", exc)
                ok = False
            if ok or XRAY_MUTATION_MODE == "grpc":
                return ok
            logger.warning("Falling back to config-file mutation after Xray gRPC failure")
        try:
            if action == "add":
                return await self.config.add_client(email, str(uuid))
            elif action == "update":
                return await self.config.update_client(email, str(uuid))
            else:
                return await self.config.remove_client(email)
        except Exception as exc:
            logger.error("Xray config-file mutation failed: %s", exc)
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
    elif update.action == "update":
        if not update.uuid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="uuid is required for update")
        ok = await mutator.update_client(email=update.telegram_id, uuid=update.uuid)
    else:
        ok = await mutator.remove_client(email=update.telegram_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="xray mutation failed")
    return {"status": "ok", "action": update.action, "telegram_id": update.telegram_id}

@app.get("/internal/xray/stats")
async def get_stats(
    request: Request,
    authorization: str = Header(alias="Authorization"),
    timestamp: str = Header(alias="X-Sync-Timestamp"),
    signature: str = Header(alias="X-Sync-Signature"),
) -> dict[str, int]:
    await _verify_request(request, authorization, timestamp, signature)
    target, _ = _load_xray_targets()
    try:
        cmd = [XRAY_BINARY, "api", "stats", "--server=" + target, "--reset"]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(f"Failed to get stats: {stderr.decode('utf-8', errors='replace')}")
            return {}
        
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        if not stdout_str:
            return {}
            
        data = json.loads(stdout_str)
        stats_dict = {}
        for item in data.get("stat", []):
            name = item.get("name", "")
            value = item.get("value", 0)
            if ">>>traffic>>>" in name:
                parts = name.split(">>>")
                if len(parts) >= 4 and parts[0] == "user":
                    email = parts[1]
                    stats_dict[email] = stats_dict.get(email, 0) + int(value)
        return stats_dict
    except Exception as exc:
        logger.error("Failed to get stats: %s", exc)
        return {}
