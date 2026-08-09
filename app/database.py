from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_mongo() -> None:
    global _client, _db
    settings = get_settings()
    _client = AsyncIOMotorClient(settings.mongodb_url)
    _db = _client[settings.mongodb_db]
    # Indexes for common query patterns
    await _db.users.create_index("username", unique=True)
    await _db.users.create_index([("status", 1), ("created_at", -1)])
    await _db.rooms.create_index("name", unique=True)
    await _db.rooms.create_index([("created_at", -1)])
    await _db.messages.create_index([("room_id", 1), ("created_at", -1)])
    await _db.messages.create_index([("sender_id", 1), ("created_at", -1)])
    await _db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    await _db.notifications.create_index([("user_id", 1), ("read", 1)])


async def close_mongo() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB is not connected")
    return _db
