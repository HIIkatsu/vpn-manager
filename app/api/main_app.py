from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers.admin_router import router as admin_router
from app.api.routers.billing_router import router as billing_router
from app.api.routers.health_router import router as health_router
from app.api.routers.subscription_router import router as subscription_router
from app.core.settings import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.ADMIN_USERNAME == "admin" or settings.ADMIN_PASSWORD == "admin":
        raise RuntimeError("Unsafe default admin credentials are forbidden")
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        raise RuntimeError("Admin credentials must not be empty")
    yield


app = FastAPI(title="AnKo VPN API", lifespan=lifespan)
app.include_router(subscription_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(health_router)
