from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from starlette.requests import Request

from app.api.routers.admin_router import router as admin_router
from app.api.routers.billing_router import router as billing_router
from app.api.routers.health_router import router as health_router
from app.api.routers.subscription_router import router as subscription_router
from app.core.logging_utils import set_request_id
from app.core.settings import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.ADMIN_USERNAME == "admin" or settings.ADMIN_PASSWORD == "admin":
        raise RuntimeError("Unsafe default admin credentials are forbidden")
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        raise RuntimeError("Admin credentials must not be empty")
    yield


app = FastAPI(title="AnKo VPN API", lifespan=lifespan)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    set_request_id(request_id)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


app.include_router(subscription_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(health_router)
