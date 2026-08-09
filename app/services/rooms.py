from typing import Any

from fastapi import HTTPException, status

from app.database import get_db
from app.models import serialize_doc, to_object_id, utcnow
from app.schemas import (
    RoomCreate,
    RoomOut,
    RoomRename,
    RoomTheme,
    ThemeType,
    RoomMembershipStatus,
    RoomMembershipOut,
)

DEFAULT_THEME = RoomTheme(type=ThemeType.PRESET, value="default")

ALLOWED_PRESETS = {
    "default",
    "midnight",
    "ocean",
    "sunset",
    "forest",
    "lavender",
    "slate",
    "rose",
}


def _theme_from_doc(doc: dict[str, Any]) -> RoomTheme:
    theme = doc.get("theme") or {}
    try:
        return RoomTheme(
            type=ThemeType(theme.get("type", "preset")),
            value=str(theme.get("value", "default")),
        )
    except Exception:
        return DEFAULT_THEME.model_copy()


def _room_out(doc: dict[str, Any]) -> RoomOut:
    data = serialize_doc(doc)
    assert data is not None
    return RoomOut(
        id=data["id"],
        name=data["name"],
        created_by=data.get("created_by"),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        theme=_theme_from_doc(doc),
    )


def _membership_out(doc: dict[str, Any]) -> RoomMembershipOut:
    data = serialize_doc(doc)
    assert data is not None
    return RoomMembershipOut(
        id=data["id"],
        room_id=data["room_id"],
        user_id=data["user_id"],
        status=data["status"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


class RoomService:
    @staticmethod
    def normalize_name(name: str) -> str:
        return name.strip().lower()

    @staticmethod
    def validate_theme(theme: RoomTheme) -> RoomTheme:
        if theme.type == ThemeType.PRESET:
            value = theme.value.strip().lower()
            if value not in ALLOWED_PRESETS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown preset. Allowed: {', '.join(sorted(ALLOWED_PRESETS))}",
                )
            return RoomTheme(type=ThemeType.PRESET, value=value)
        if theme.type == ThemeType.SOLID:
            value = theme.value.strip()
            if not value.startswith("#") or len(value) not in (4, 7):
                raise HTTPException(
                    status_code=400,
                    detail="Solid theme requires a hex color like #1a2332",
                )
            return RoomTheme(type=ThemeType.SOLID, value=value)
        # image
        value = theme.value.strip()
        if not (value.startswith("http://") or value.startswith("https://") or value.startswith("data:image/")):
            raise HTTPException(
                status_code=400,
                detail="Image theme requires an http(s) URL or data:image URL",
            )
        return RoomTheme(type=ThemeType.IMAGE, value=value)

    @staticmethod
    async def ensure_default_room() -> None:
        db = get_db()
        existing = await db.rooms.find_one({"name": "general"})
        if existing:
            if "theme" not in existing:
                await db.rooms.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"theme": DEFAULT_THEME.model_dump()}},
                )
            return
        now = utcnow()
        await db.rooms.insert_one(
            {
                "name": "general",
                "created_by": None,
                "created_at": now,
                "updated_at": now,
                "theme": DEFAULT_THEME.model_dump(),
            }
        )

    @staticmethod
    async def get_by_name(name: str) -> dict[str, Any] | None:
        db = get_db()
        return await db.rooms.find_one({"name": RoomService.normalize_name(name)})

    @staticmethod
    async def get_by_id(room_id: str) -> dict[str, Any] | None:
        db = get_db()
        try:
            oid = to_object_id(room_id)
        except Exception:
            return None
        return await db.rooms.find_one({"_id": oid})

    @staticmethod
    async def require_exists(name: str) -> RoomOut:
        doc = await RoomService.get_by_name(name)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Room '{name}' does not exist",
            )
        return _room_out(doc)

    @staticmethod
    async def list_rooms() -> list[RoomOut]:
        db = get_db()
        cursor = db.rooms.find({}).sort("name", 1)
        docs = await cursor.to_list(length=500)
        return [_room_out(d) for d in docs]

    @staticmethod
    async def create_room(payload: RoomCreate, *, admin_id: str) -> RoomOut:
        db = get_db()
        name = RoomService.normalize_name(payload.name)
        existing = await db.rooms.find_one({"name": name})
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Room name already exists",
            )
        now = utcnow()
        doc = {
            "name": name,
            "created_by": admin_id,
            "created_at": now,
            "updated_at": now,
            "theme": DEFAULT_THEME.model_dump(),
        }
        result = await db.rooms.insert_one(doc)
        doc["_id"] = result.inserted_id
        
        # Auto-approve the creator
        await db.room_memberships.insert_one({
            "room_id": name,
            "user_id": admin_id,
            "status": RoomMembershipStatus.APPROVED.value,
            "created_at": now,
            "updated_at": now,
        })
        
        return _room_out(doc)

    @staticmethod
    async def rename_room(room_id: str, payload: RoomRename) -> tuple[RoomOut, str]:
        db = get_db()
        doc = await RoomService.get_by_id(room_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Room not found")

        old_name = doc["name"]
        new_name = RoomService.normalize_name(payload.name)
        if new_name == old_name:
            return _room_out(doc), old_name

        clash = await db.rooms.find_one({"name": new_name})
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Room name already exists",
            )

        updated = await db.rooms.find_one_and_update(
            {"_id": doc["_id"]},
            {"$set": {"name": new_name, "updated_at": utcnow()}},
            return_document=True,
        )
        await db.messages.update_many(
            {"room_id": old_name},
            {"$set": {"room_id": new_name}},
        )
        await db.room_memberships.update_many(
            {"room_id": old_name},
            {"$set": {"room_id": new_name}},
        )
        return _room_out(updated), old_name

    @staticmethod
    async def set_theme(room_id: str, theme: RoomTheme) -> RoomOut:
        db = get_db()
        doc = await RoomService.get_by_id(room_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Room not found")
        validated = RoomService.validate_theme(theme)
        updated = await db.rooms.find_one_and_update(
            {"_id": doc["_id"]},
            {"$set": {"theme": validated.model_dump(), "updated_at": utcnow()}},
            return_document=True,
        )
        return _room_out(updated)

    @staticmethod
    async def delete_room(room_id: str) -> RoomOut:
        db = get_db()
        doc = await RoomService.get_by_id(room_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Room not found")

        if doc["name"] == "general":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the default 'general' room",
            )

        await db.messages.delete_many({"room_id": doc["name"]})
        await db.room_memberships.delete_many({"room_id": doc["name"]})
        await db.rooms.delete_one({"_id": doc["_id"]})
        return _room_out(doc)

    @staticmethod
    async def request_join(room_id: str, user_id: str) -> RoomMembershipOut:
        db = get_db()
        room = await RoomService.require_exists(room_id)
        existing = await db.room_memberships.find_one({"room_id": room.name, "user_id": user_id})
        if existing:
            return _membership_out(existing)
            
        now = utcnow()
        doc = {
            "room_id": room.name,
            "user_id": user_id,
            "status": RoomMembershipStatus.PENDING.value,
            "created_at": now,
            "updated_at": now,
        }
        res = await db.room_memberships.insert_one(doc)
        doc["_id"] = res.inserted_id
        return _membership_out(doc)
        
    @staticmethod
    async def approve_join(room_id: str, user_id: str) -> RoomMembershipOut:
        db = get_db()
        room = await RoomService.require_exists(room_id)
        doc = await db.room_memberships.find_one_and_update(
            {"room_id": room.name, "user_id": user_id},
            {"$set": {"status": RoomMembershipStatus.APPROVED.value, "updated_at": utcnow()}},
            return_document=True,
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Membership request not found")
        return _membership_out(doc)

    @staticmethod
    async def remove_user(room_id: str, user_id: str) -> None:
        db = get_db()
        room = await RoomService.require_exists(room_id)
        res = await db.room_memberships.delete_one({"room_id": room.name, "user_id": user_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="User is not a member or request not found")

    @staticmethod
    async def get_membership(room_id: str, user_id: str) -> RoomMembershipOut | None:
        db = get_db()
        doc = await db.room_memberships.find_one({"room_id": room_id, "user_id": user_id})
        if doc:
            return _membership_out(doc)
        return None

    @staticmethod
    async def list_memberships(room_id: str) -> list[RoomMembershipOut]:
        db = get_db()
        room = await RoomService.require_exists(room_id)
        cursor = db.room_memberships.find({"room_id": room.name}).sort("created_at", -1)
        docs = await cursor.to_list(length=1000)
        return [_membership_out(d) for d in docs]

