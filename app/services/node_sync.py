import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.settings import settings
from app.services.xray_manager import XrayManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncNode:
    name: str
    url: str


def _configured_nodes() -> list[SyncNode]:
    raw = (settings.SYNC_PUSH_NODES or "").strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    nodes: list[SyncNode] = []
    if isinstance(parsed, list):
        for index, item in enumerate(parsed, start=1):
            if isinstance(item, str):
                url = item.strip()
                name = f"node-{index}"
            elif isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                name = str(item.get("name") or f"node-{index}").strip()
            else:
                continue
            if url:
                nodes.append(SyncNode(name=name, url=url.rstrip("/")))
        return nodes

    for index, url in enumerate((part.strip() for part in raw.split(",")), start=1):
        if url:
            nodes.append(SyncNode(name=f"node-{index}", url=url.rstrip("/")))
    return nodes


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


class ActivePushDispatcher:
    def __init__(self, *, xray: XrayManager | None = None, nodes: list[SyncNode] | None = None) -> None:
        self.xray = xray or XrayManager()
        self.nodes = nodes if nodes is not None else _configured_nodes()
        self.timeout = httpx.Timeout(settings.SYNC_PUSH_TIMEOUT_SECONDS)

    async def add_client(self, *, telegram_id: int | str, uuid: str, event_id: str | int | None = None) -> tuple[bool, str | None]:
        payload = {"action": "add", "telegram_id": str(telegram_id), "uuid": uuid, "event_id": str(event_id or "")}
        return await self._apply_everywhere(payload)

    async def remove_client(self, *, telegram_id: int | str, event_id: str | int | None = None) -> tuple[bool, str | None]:
        payload = {"action": "remove", "telegram_id": str(telegram_id), "event_id": str(event_id or "")}
        return await self._apply_everywhere(payload)

    async def _apply_everywhere(self, payload: dict[str, Any]) -> tuple[bool, str | None]:
        if payload["action"] == "add":
            local_ok = await self.xray.add_client(email=str(payload["telegram_id"]), uuid=str(payload["uuid"]))
        elif payload["action"] == "remove":
            local_ok = await self.xray.remove_client(email=str(payload["telegram_id"]))
        else:
            return False, f"unsupported action {payload['action']}"

        if not local_ok:
            return False, "local xray call returned false"

        remote_errors = await self._push_to_nodes(payload)
        if remote_errors:
            return False, "; ".join(remote_errors)
        return True, None

    async def _push_to_nodes(self, payload: dict[str, Any]) -> list[str]:
        if not self.nodes:
            return []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(
                *(self._push_one(client, node, payload) for node in self.nodes),
                return_exceptions=True,
            )

        errors: list[str] = []
        for node, result in zip(self.nodes, results, strict=False):
            if isinstance(result, Exception):
                errors.append(f"{node.name}: {result}")
            elif result:
                errors.append(f"{node.name}: {result}")
        return errors

    async def _push_one(self, client: httpx.AsyncClient, node: SyncNode, payload: dict[str, Any]) -> str | None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Authorization": f"Bearer {settings.SYNC_NODES_TOKEN}",
            "Content-Type": "application/json",
            "X-Sync-Timestamp": timestamp,
            "X-Sync-Signature": _signature(settings.SYNC_NODES_TOKEN, timestamp, body),
        }
        response = await client.post(node.url, content=body, headers=headers)
        if response.status_code == 200:
            return None
        return f"HTTP {response.status_code} {response.text[:300]}"
