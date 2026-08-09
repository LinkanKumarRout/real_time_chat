"""Helpers to deliver events to local sockets and Redis."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.connection_manager import manager
from app.services.redis_pubsub import redis_pubsub

logger = logging.getLogger(__name__)

# Suppress Redis echo duplicates on the same process (local emit + Redis fan-out).
_recent_keys: dict[str, float] = {}
_DEDUP_TTL_SEC = 3.0


def _event_key(event: str, data: dict[str, Any]) -> str:
    if data.get("id"):
        return f"{event}:{data['id']}"
    room = data.get("room_id", "")
    user = data.get("user_id", "")
    content = data.get("content", data.get("body", ""))
    return f"{event}:{room}:{user}:{content}:{data.get('created_at', '')}"


def _prune(now: float) -> None:
    stale = [k for k, ts in _recent_keys.items() if now - ts > _DEDUP_TTL_SEC]
    for k in stale:
        _recent_keys.pop(k, None)


def mark_emitted(event: str, data: dict[str, Any]) -> str:
    key = _event_key(event, data)
    now = time.monotonic()
    _prune(now)
    _recent_keys[key] = now
    return key


def was_recently_emitted(event: str, data: dict[str, Any]) -> bool:
    key = _event_key(event, data)
    now = time.monotonic()
    _prune(now)
    ts = _recent_keys.get(key)
    return ts is not None and (now - ts) <= _DEDUP_TTL_SEC


async def emit_room(room_id: str, event: str, data: dict[str, Any]) -> None:
    """Push to local WebSocket clients immediately, then fan-out via Redis."""
    payload = {"event": event, "data": data}
    mark_emitted(event, data)
    # Always deliver locally first so chat stays real-time even if Redis fails.
    await manager.broadcast_room(room_id, payload)
    channel = redis_pubsub.channel_for_room(room_id)
    try:
        await redis_pubsub.subscribe(channel)
        await redis_pubsub.publish(channel, payload)
    except Exception:
        logger.exception("Redis fan-out failed for room %s (local delivery OK)", room_id)


async def emit_user(user_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data}
    mark_emitted(event, data)
    await manager.broadcast_user(user_id, payload)
    channel = redis_pubsub.channel_for_user(user_id)
    try:
        await redis_pubsub.subscribe(channel)
        await redis_pubsub.publish(channel, payload)
    except Exception:
        logger.exception("Redis fan-out failed for user %s (local delivery OK)", user_id)


async def emit_global(event: str, data: dict[str, Any]) -> None:
    """Notify every connected client (room list changes, etc.)."""
    payload = {"event": event, "data": data}
    mark_emitted(event, data)
    await manager.broadcast_all(payload)
    channel = redis_pubsub.channel_global()
    try:
        await redis_pubsub.subscribe(channel)
        await redis_pubsub.publish(channel, payload)
    except Exception:
        logger.exception("Redis global fan-out failed (local delivery OK)")
