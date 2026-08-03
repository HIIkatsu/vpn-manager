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

    async def add_client(self, *, telegram_id: int | str, uuid: str, event_id: str | int | None = None, target_nodes: list[str] | None = None) -> tuple[bool, str | None]:
        payload = {"action": "add", "telegram_id": str(telegram_id), "uuid": uuid, "event_id": str(event_id or "")}
        return await self._apply_everywhere(payload, target_nodes=target_nodes)

    async def remove_client(self, *, telegram_id: int | str, event_id: str | int | None = None, target_nodes: list[str] | None = None) -> tuple[bool, str | None]:
        payload = {"action": "remove", "telegram_id": str(telegram_id), "event_id": str(event_id or "")}
        return await self._apply_everywhere(payload, target_nodes=target_nodes)

    async def update_client(self, *, telegram_id: int | str, uuid: str, event_id: str | int | None = None, target_nodes: list[str] | None = None) -> tuple[bool, str | None]:
        payload = {"action": "update", "telegram_id": str(telegram_id), "uuid": uuid, "event_id": str(event_id or "")}
        return await self._apply_everywhere(payload, target_nodes=target_nodes)

    async def get_stats(self) -> dict[str, int]:
        if not self.nodes:
            return {}
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(
                *(self._fetch_stats_one(client, node) for node in self.nodes),
                return_exceptions=True,
            )
            
        combined_stats = {}
        for result in results:
            if isinstance(result, dict):
                for k, v in result.items():
                    combined_stats[k] = combined_stats.get(k, 0) + int(v)
        return combined_stats

    async def _fetch_stats_one(self, client: httpx.AsyncClient, node: SyncNode) -> dict[str, int]:
        timestamp = str(int(time.time()))
        headers = {
            "Authorization": f"Bearer {settings.SYNC_NODES_TOKEN}",
            "X-Sync-Timestamp": timestamp,
            "X-Sync-Signature": _signature(settings.SYNC_NODES_TOKEN, timestamp, b""),
        }
        try:
            response = await client.get(f"{node.url.replace('/internal/xray/client', '')}/internal/xray/stats", headers=headers)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                # Node has old version, ignore
                return {}
        except Exception as exc:
            logger.warning(f"Failed to fetch stats from {node.name}: {exc}")
        return {}

    async def _apply_everywhere(self, payload: dict[str, Any], target_nodes: list[str] | None = None) -> tuple[bool, str | None]:
        local_ok = True
        failed_nodes = []

        if target_nodes is None or "local" in target_nodes:
            if payload["action"] == "add":
                local_ok = await self.xray.add_client(email=str(payload["telegram_id"]), uuid=str(payload["uuid"]))
            elif payload["action"] == "remove":
                local_ok = await self.xray.remove_client(email=str(payload["telegram_id"]))
            elif payload["action"] == "update":
                # For local Xray Manager, we don't have an update_client yet, so we just remove and add
                await self.xray.remove_client(email=str(payload["telegram_id"]))
                local_ok = await self.xray.add_client(email=str(payload["telegram_id"]), uuid=str(payload["uuid"]))
            else:
                return False, f"unsupported action {payload['action']}"

            if not local_ok:
                failed_nodes.append("local")

        remote_errors, failed_remotes = await self._push_to_nodes(payload, target_nodes=target_nodes)
        
        if remote_errors:
            logger.warning(f"Push to remote nodes had errors: {remote_errors}")
            
        failed_nodes.extend(failed_remotes)
        
        if failed_nodes:
            # We return False and JSON array of failed nodes so Outbox can retry ONLY them
            return False, json.dumps(failed_nodes)
            
        return True, None

    async def _push_to_nodes(self, payload: dict[str, Any], target_nodes: list[str] | None = None) -> tuple[list[str], list[str]]:
        nodes_to_push = self.nodes
        if target_nodes is not None:
            nodes_to_push = [n for n in self.nodes if n.name in target_nodes]

        if not nodes_to_push:
            return [], []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(
                *(self._push_one(client, node, payload) for node in nodes_to_push),
                return_exceptions=True,
            )

        errors: list[str] = []
        failed_node_names: list[str] = []
        for node, result in zip(nodes_to_push, results, strict=False):
            if isinstance(result, Exception):
                errors.append(f"{node.name}: {result}")
                failed_node_names.append(node.name)
            elif result:
                errors.append(f"{node.name}: {result}")
                failed_node_names.append(node.name)
        return errors, failed_node_names

    async def _push_one(self, client: httpx.AsyncClient, node: SyncNode, payload: dict[str, Any]) -> str | None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Authorization": f"Bearer {settings.SYNC_NODES_TOKEN}",
            "Content-Type": "application/json",
            "X-Sync-Timestamp": timestamp,
            "X-Sync-Signature": _signature(settings.SYNC_NODES_TOKEN, timestamp, body),
        }
        try:
            response = await client.post(node.url, content=body, headers=headers)
            if response.status_code in (422, 500) and payload["action"] == "update":
                rem_payload = {"action": "remove", "telegram_id": payload["telegram_id"], "event_id": payload.get("event_id", "")}
                rem_body = json.dumps(rem_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                rem_headers = headers.copy()
                rem_headers["X-Sync-Signature"] = _signature(settings.SYNC_NODES_TOKEN, timestamp, rem_body)
                await client.post(node.url, content=rem_body, headers=rem_headers)

                add_payload = {"action": "add", "telegram_id": payload["telegram_id"], "uuid": payload["uuid"], "event_id": payload.get("event_id", "")}
                add_body = json.dumps(add_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                add_headers = headers.copy()
                add_headers["X-Sync-Signature"] = _signature(settings.SYNC_NODES_TOKEN, timestamp, add_body)
                response = await client.post(node.url, content=add_body, headers=add_headers)
            
            response.raise_for_status()
            return None
        except Exception as e:
            return f"HTTP/Error {e}"
