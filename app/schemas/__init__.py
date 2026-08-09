from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TEXT = "text"
    SYSTEM = "system"
    NOTIFICATION = "notification"


class NotificationType(str, Enum):
    MESSAGE = "message"
    MENTION = "mention"
    SYSTEM = "system"
    CUSTOM = "custom"


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class UserStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class UserCreate(BaseModel):
    """Admin-only: create a new user account (starts as pending)."""

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    role: UserRole = UserRole.USER


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    status: UserStatus
    created_at: datetime
    approved_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserListResponse(BaseModel):
    total: int
    users: list[UserOut]


class RoomCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class RoomRename(BaseModel):
    name: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class ThemeType(str, Enum):
    SOLID = "solid"
    IMAGE = "image"
    PRESET = "preset"


class RoomTheme(BaseModel):
    type: ThemeType = ThemeType.PRESET
    value: str = Field(default="default", min_length=1, max_length=500)


class RoomThemeUpdate(BaseModel):
    theme: RoomTheme


class RoomOut(BaseModel):
    id: str
    name: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    theme: RoomTheme = Field(default_factory=RoomTheme)


class RoomListResponse(BaseModel):
    total: int
    rooms: list[RoomOut]


class RoomMembershipStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RoomMembershipOut(BaseModel):
    id: str
    room_id: str
    user_id: str
    status: RoomMembershipStatus
    created_at: datetime
    updated_at: datetime


class RoomMembershipListResponse(BaseModel):
    total: int
    memberships: list[RoomMembershipOut]


class ReactionUser(BaseModel):
    user_id: str
    user_name: str


class ReactionToggle(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=16)


class MessageCreate(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=4000)
    message_type: MessageType = MessageType.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageOut(BaseModel):
    id: str
    room_id: str
    sender_id: str
    sender_name: str
    content: str
    message_type: MessageType
    metadata: dict[str, Any] = Field(default_factory=dict)
    reactions: dict[str, list[ReactionUser]] = Field(default_factory=dict)
    created_at: datetime


class MessageListResponse(BaseModel):
    room_id: str
    total: int
    limit: int
    skip: int
    messages: list[MessageOut]


class NotificationCreate(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=256)
    body: str = Field(..., min_length=1, max_length=2000)
    notification_type: NotificationType = NotificationType.CUSTOM
    data: dict[str, Any] = Field(default_factory=dict)


class NotificationOut(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    notification_type: NotificationType
    data: dict[str, Any] = Field(default_factory=dict)
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    user_id: str
    total: int
    unread_count: int
    limit: int
    skip: int
    notifications: list[NotificationOut]


class HealthResponse(BaseModel):
    status: str
    mongodb: str
    redis: str
    app: str


class WSIncoming(BaseModel):
    """Client -> server WebSocket payload."""

    action: str  # join | leave | send_message | react | ping
    room_id: str | None = None
    content: str | None = None
    message_id: str | None = None
    emoji: str | None = None
    message_type: MessageType = MessageType.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)


class WSOutgoing(BaseModel):
    """Server -> client WebSocket payload."""

    event: str
    data: dict[str, Any] = Field(default_factory=dict)
