from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from app.core.container import get_billing_service, get_user_service
from app.db.database import async_session_maker
from app.services.transaction import session_scope


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Any, dict[str, Any]], Awaitable[Any]], event: Any, data: dict[str, Any]) -> Any:
        async with session_scope(async_session_maker) as session:
            data["session"] = session
            data["user_service"] = get_user_service(session)
            data["billing_service"] = get_billing_service(session)
            return await handler(event, data)
