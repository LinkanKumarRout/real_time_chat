from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas import (
    MessageCreate,
    MessageListResponse,
    MessageOut,
    NotificationCreate,
    NotificationListResponse,
    NotificationOut,
    ReactionToggle,
    UserOut,
)
from app.services.auth import get_current_user, require_admin
from app.services.chat import MessageService, NotificationService
from app.services.realtime import emit_room, emit_user
from app.services.rooms import RoomService

router = APIRouter(prefix="/api", tags=["messages"])


@router.post(
    "/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a chat message and broadcast it in real time",
)
async def create_message(
    payload: MessageCreate,
    user: UserOut = Depends(get_current_user),
) -> MessageOut:
    room_name = RoomService.normalize_name(payload.room_id)
    await RoomService.require_exists(room_name)
    message = await MessageService.create_message(
        room_id=room_name,
        sender_id=user.id,
        sender_name=user.display_name,
        content=payload.content,
        message_type=payload.message_type,
        metadata=payload.metadata,
    )
    await emit_room(room_name, "message", message.model_dump(mode="json"))
    return message


@router.get(
    "/messages/{room_id}",
    response_model=MessageListResponse,
    summary="Retrieve chat history for a room",
)
async def get_room_messages(
    room_id: str,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    _: UserOut = Depends(get_current_user),
) -> MessageListResponse:
    messages, total = await MessageService.list_messages(
        room_id, limit=limit, skip=skip
    )
    return MessageListResponse(
        room_id=room_id,
        total=total,
        limit=limit,
        skip=skip,
        messages=messages,
    )


@router.get(
    "/messages/detail/{message_id}",
    response_model=MessageOut,
    summary="Get a single message by id",
)
async def get_message(
    message_id: str,
    _: UserOut = Depends(get_current_user),
) -> MessageOut:
    message = await MessageService.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@router.post(
    "/messages/{message_id}/reactions",
    response_model=MessageOut,
    summary="Toggle a reaction on a message",
)
async def toggle_reaction(
    message_id: str,
    payload: ReactionToggle,
    user: UserOut = Depends(get_current_user),
) -> MessageOut:
    message = await MessageService.toggle_reaction(
        message_id=message_id,
        emoji=payload.emoji,
        user_id=user.id,
        user_name=user.display_name,
    )
    await emit_room(
        message.room_id,
        "reaction_updated",
        message.model_dump(mode="json"),
    )
    return message


@router.delete(
    "/messages/by-id/{message_id}",
    response_model=MessageOut,
    summary="Delete a message (own message, or admin)",
)
async def delete_message(
    message_id: str,
    user: UserOut = Depends(get_current_user),
) -> MessageOut:
    message = await MessageService.delete_message(
        message_id=message_id,
        user_id=user.id,
        is_admin=user.role.value == "admin",
    )
    await emit_room(
        message.room_id,
        "message_deleted",
        {"id": message.id, "room_id": message.room_id},
    )
    return message


@router.post(
    "/notifications",
    response_model=NotificationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification and push it to the user over WebSocket",
    tags=["notifications"],
)
async def create_notification(
    payload: NotificationCreate,
    _: UserOut = Depends(require_admin),
) -> NotificationOut:
    notification = await NotificationService.create_notification(payload)
    await emit_user(
        payload.user_id,
        "notification",
        notification.model_dump(mode="json"),
    )
    return notification


@router.get(
    "/notifications/{user_id}",
    response_model=NotificationListResponse,
    summary="List notifications for a user",
    tags=["notifications"],
)
async def list_notifications(
    user_id: str,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    current: UserOut = Depends(get_current_user),
) -> NotificationListResponse:
    if current.id != user_id and current.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    notifications, total, unread_count = await NotificationService.list_notifications(
        user_id, limit=limit, skip=skip, unread_only=unread_only
    )
    return NotificationListResponse(
        user_id=user_id,
        total=total,
        unread_count=unread_count,
        limit=limit,
        skip=skip,
        notifications=notifications,
    )


@router.patch(
    "/notifications/{user_id}/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark a notification as read",
    tags=["notifications"],
)
async def mark_notification_read(
    user_id: str,
    notification_id: str,
    current: UserOut = Depends(get_current_user),
) -> NotificationOut:
    if current.id != user_id and current.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    notification = await NotificationService.mark_read(notification_id, user_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification


@router.post(
    "/notifications/{user_id}/read-all",
    summary="Mark all notifications as read for a user",
    tags=["notifications"],
)
async def mark_all_notifications_read(
    user_id: str,
    current: UserOut = Depends(get_current_user),
) -> dict[str, int]:
    if current.id != user_id and current.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    modified = await NotificationService.mark_all_read(user_id)
    return {"modified_count": modified}
