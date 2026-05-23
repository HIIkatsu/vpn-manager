from __future__ import annotations

from typing import Any


def log_context(**kwargs: Any) -> dict[str, Any]:
    """Build a consistent structured log context payload."""
    return {
        "request_id": kwargs.get("request_id"),
        "payment_id": kwargs.get("payment_id"),
        "telegram_id": kwargs.get("telegram_id"),
        "event_id": kwargs.get("event_id"),
        "action_source": kwargs.get("action_source"),
    }
