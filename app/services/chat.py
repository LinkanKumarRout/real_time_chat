from typing import Any

from fastapi import HTTPException

from app.database import get_db
from app.models import serialize_doc, to_object_id, utcnow
from app.schemas import (
    MessageOut,
    MessageType,
    NotificationCreate,
    NotificationOut,
    ReactionUser,
)


def _message_out(doc: dict[str, Any]) -> MessageOut:
    data = serialize_doc(doc)
    assert data is not None
    raw_reactions = data.get("reactions") or {}
    reactions: dict[str, list[ReactionUser]] = {}
    if isinstance(raw_reactions, dict):
        for emoji, users in raw_reactions.items():
            cleaned: list[ReactionUser] = []
            if isinstance(users, list):
                for u in users:
                    if isinstance(u, dict) and u.get("user_id"):
                        cleaned.append(
                            ReactionUser(
                                user_id=str(u["user_id"]),
                                user_name=str(u.get("user_name") or "User"),
                            )
                        )
            reactions[str(emoji)] = cleaned
    return MessageOut(
        id=data["id"],
        room_id=data["room_id"],
        sender_id=data["sender_id"],
        sender_name=data["sender_name"],
        content=data["content"],
        message_type=MessageType(data.get("message_type", "text")),
        metadata=data.get("metadata") or {},
        reactions=reactions,
        created_at=data["created_at"],
    )


class MessageService:
    @staticmethod
    async def create_message(
        *,
        room_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        metadata: dict[str, Any] | None = None,
    ) -> MessageOut:
        db = get_db()
        doc: dict[str, Any] = {
            "room_id": room_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "message_type": message_type.value,
            "metadata": metadata or {},
            "reactions": {},
            "created_at": utcnow(),
        }
        result = await db.messages.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _message_out(doc)

    @staticmethod
    async def list_messages(
        room_id: str,
        *,
        limit: int = 50,
        skip: int = 0,
    ) -> tuple[list[MessageOut], int]:
        db = get_db()
        query = {"room_id": room_id}
        total = await db.messages.count_documents(query)
        cursor = (
            db.messages.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        docs.reverse()
        return [_message_out(d) for d in docs], total

    @staticmethod
    async def get_message(message_id: str) -> MessageOut | None:
        db = get_db()
        try:
            oid = to_object_id(message_id)
        except Exception:
            return None
        doc = await db.messages.find_one({"_id": oid})
        if not doc:
            return None
        return _message_out(doc)

    @staticmethod
    async def toggle_reaction(
        *,
        message_id: str,
        emoji: str,
        user_id: str,
        user_name: str,
    ) -> MessageOut:
        db = get_db()
        try:
            oid = to_object_id(message_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Message not found") from exc

        doc = await db.messages.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Message not found")

        emoji = emoji.strip()
        if not emoji:
            raise HTTPException(status_code=400, detail="Emoji required")

        reactions: dict[str, list[dict[str, str]]] = dict(doc.get("reactions") or {})
        users = list(reactions.get(emoji) or [])
        existing_idx = next(
            (i for i, u in enumerate(users) if u.get("user_id") == user_id),
            None,
        )
        if existing_idx is None:
            users.append({"user_id": user_id, "user_name": user_name})
            reactions[emoji] = users
        else:
            users.pop(existing_idx)
            if users:
                reactions[emoji] = users
            else:
                reactions.pop(emoji, None)

        updated = await db.messages.find_one_and_update(
            {"_id": oid},
            {"$set": {"reactions": reactions}},
            return_document=True,
        )
        return _message_out(updated)

    @staticmethod
    async def delete_message(
        *,
        message_id: str,
        user_id: str,
        is_admin: bool = False,
    ) -> MessageOut:
        db = get_db()
        try:
            oid = to_object_id(message_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Message not found") from exc

        doc = await db.messages.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Message not found")

        if doc.get("sender_id") != user_id and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="You can only delete your own messages",
            )

        message = _message_out(doc)
        await db.messages.delete_one({"_id": oid})
        return message

    @staticmethod
    async def clear_room(room_id: str) -> int:
        db = get_db()
        result = await db.messages.delete_many({"room_id": room_id})
        return result.deleted_count


class NotificationService:
    @staticmethod
    async def create_notification(payload: NotificationCreate) -> NotificationOut:
        db = get_db()
        doc: dict[str, Any] = {
            "user_id": payload.user_id,
            "title": payload.title,
            "body": payload.body,
            "notification_type": payload.notification_type.value,
            "data": payload.data,
            "read": False,
            "created_at": utcnow(),
        }
        result = await db.notifications.insert_one(doc)
        doc["_id"] = result.inserted_id
        return NotificationOut(**serialize_doc(doc))  # type: ignore[arg-type]

    @staticmethod
    async def list_notifications(
        user_id: str,
        *,
        limit: int = 50,
        skip: int = 0,
        unread_only: bool = False,
    ) -> tuple[list[NotificationOut], int, int]:
        db = get_db()
        query: dict[str, Any] = {"user_id": user_id}
        if unread_only:
            query["read"] = False

        total = await db.notifications.count_documents({"user_id": user_id})
        unread_count = await db.notifications.count_documents(
            {"user_id": user_id, "read": False}
        )
        cursor = (
            db.notifications.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        notifications = [NotificationOut(**serialize_doc(d)) for d in docs]  # type: ignore[arg-type]
        return notifications, total, unread_count

    @staticmethod
    async def mark_read(notification_id: str, user_id: str) -> NotificationOut | None:
        db = get_db()
        try:
            oid = to_object_id(notification_id)
        except Exception:
            return None

        doc = await db.notifications.find_one_and_update(
            {"_id": oid, "user_id": user_id},
            {"$set": {"read": True}},
            return_document=True,
        )
        if not doc:
            return None
        return NotificationOut(**serialize_doc(doc))  # type: ignore[arg-type]

    @staticmethod
    async def mark_all_read(user_id: str) -> int:
        db = get_db()
        result = await db.notifications.update_many(
            {"user_id": user_id, "read": False},
            {"$set": {"read": True}},
        )
        return result.modified_count
