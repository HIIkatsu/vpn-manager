from __future__ import annotations

from contextvars import ContextVar
from typing import Any


_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> str | None:
    return _request_id_ctx.get()


def log_context(**kwargs: Any) -> dict[str, Any]:
    """Build a consistent structured log context payload."""
    request_id = kwargs.get("request_id") or get_request_id()
    return {
        "request_id": request_id,
        "payment_id": kwargs.get("payment_id"),
        "telegram_id": kwargs.get("telegram_id"),
        "event_id": kwargs.get("event_id"),
        "action_source": kwargs.get("action_source"),
    }
