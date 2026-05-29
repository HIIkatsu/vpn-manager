import secrets
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.database import async_session_maker
from app.services.transaction import session_scope

security = HTTPBasic()


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_write_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_scope(async_session_maker) as session:
        yield session


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_write_session():
        yield session


def get_current_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_username = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
