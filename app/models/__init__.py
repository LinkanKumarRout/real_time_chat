from datetime import datetime, timezone
from typing import Any

from bson import ObjectId


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Any) -> Any:
    """Make datetimes timezone-aware UTC so JSON always includes offset."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
        else:
            out[key] = ensure_utc(value)
    return out


def to_object_id(value: str) -> ObjectId:
    return ObjectId(value)
