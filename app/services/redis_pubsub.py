import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class RedisPubSub:
    """Redis pub/sub bridge used to fan-out events across WebSocket workers."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._pubsub_redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._listener_task: asyncio.Task | None = None
        self._handlers: list[MessageHandler] = []
        self._subscribed_channels: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("Redis is not connected")
        return self._redis

    async def connect(self) -> None:
        settings = get_settings()
        # Separate connections: command client vs pub/sub listener
        self._redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        self._pubsub_redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._redis.ping()
        await self._pubsub_redis.ping()
        self._pubsub = self._pubsub_redis.pubsub(ignore_subscribe_messages=True)
        self._listener_task = asyncio.create_task(self._listen())

    async def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._pubsub is not None:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
            self._pubsub = None

        if self._pubsub_redis is not None:
            await self._pubsub_redis.aclose()
            self._pubsub_redis = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        self._subscribed_channels.clear()

    def add_handler(self, handler: MessageHandler) -> None:
        self._handlers.append(handler)

    def channel_for_room(self, room_id: str) -> str:
        prefix = get_settings().redis_channel_prefix
        return f"{prefix}:room:{room_id}"

    def channel_for_user(self, user_id: str) -> str:
        prefix = get_settings().redis_channel_prefix
        return f"{prefix}:user:{user_id}"

    def channel_global(self) -> str:
        prefix = get_settings().redis_channel_prefix
        return f"{prefix}:global"

    async def subscribe(self, channel: str) -> None:
        if self._pubsub is None:
            raise RuntimeError("Redis pub/sub is not ready")
        async with self._lock:
            if channel in self._subscribed_channels:
                return
            await self._pubsub.subscribe(channel)
            self._subscribed_channels.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        if self._pubsub is None:
            return
        async with self._lock:
            if channel not in self._subscribed_channels:
                return
            await self._pubsub.unsubscribe(channel)
            self._subscribed_channels.discard(channel)

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        await self.redis.publish(channel, json.dumps(payload, default=str))

    async def _listen(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue

                channel = message.get("channel")
                data_raw = message.get("data")
                if not channel or data_raw is None:
                    continue

                try:
                    data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
                except (json.JSONDecodeError, TypeError):
                    continue

                for handler in list(self._handlers):
                    try:
                        await handler(channel, data)
                    except Exception:
                        logger.exception("Redis handler failed for channel %s", channel)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Redis pub/sub listener crashed; restarting in 1s")
            await asyncio.sleep(1.0)
            self._listener_task = asyncio.create_task(self._listen())


redis_pubsub = RedisPubSub()
