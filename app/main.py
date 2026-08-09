from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import close_mongo, connect_mongo, get_db
from app.routers import api, auth, rooms, ws
from app.schemas import HealthResponse
from app.services.auth import AuthService
from app.services.connection_manager import manager
from app.services.realtime import was_recently_emitted
from app.services.redis_pubsub import redis_pubsub
from app.services.rooms import RoomService

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _on_redis_message(channel: str, payload: dict[str, Any]) -> None:
    """Fan-out Redis pub/sub events to local WebSocket clients."""
    event = payload.get("event")
    data = payload.get("data") or {}
    if not event:
        return

    # Skip echo of events this process already pushed locally
    if was_recently_emitted(str(event), data):
        return

    prefix = get_settings().redis_channel_prefix
    room_prefix = f"{prefix}:room:"
    user_prefix = f"{prefix}:user:"
    global_channel = f"{prefix}:global"

    if channel == global_channel:
        await manager.broadcast_all({"event": event, "data": data})
    elif channel.startswith(room_prefix):
        room_id = channel[len(room_prefix) :]
        await manager.broadcast_room(room_id, {"event": event, "data": data})
    elif channel.startswith(user_prefix):
        user_id = channel[len(user_prefix) :]
        await manager.broadcast_user(user_id, {"event": event, "data": data})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await connect_mongo()
    await AuthService.ensure_admin_seed()
    await RoomService.ensure_default_room()
    await redis_pubsub.connect()
    redis_pubsub.add_handler(_on_redis_message)
    yield
    await redis_pubsub.close()
    await close_mongo()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        description=(
            "Real-time messaging with admin-approved users, FastAPI WebSockets, "
            "Redis pub/sub, and MongoDB chat history."
        ),
        version="1.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(auth.router)
    application.include_router(rooms.router)
    application.include_router(api.router)
    application.include_router(ws.router)

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    async def demo_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        mongo_status = "ok"
        redis_status = "ok"
        try:
            await get_db().command("ping")
        except Exception:
            mongo_status = "error"
        try:
            await redis_pubsub.redis.ping()
        except Exception:
            redis_status = "error"

        overall = (
            "healthy"
            if mongo_status == "ok" and redis_status == "ok"
            else "degraded"
        )
        return HealthResponse(
            status=overall,
            mongodb=mongo_status,
            redis=redis_status,
            app=settings.app_name,
        )

    return application


app = create_app()
