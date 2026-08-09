import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from app.schemas import MessageType, WSIncoming, RoomMembershipStatus, UserRole
from app.services.auth import get_user_from_token_string
from app.services.chat import MessageService
from app.services.connection_manager import manager
from app.services.realtime import emit_room
from app.services.redis_pubsub import redis_pubsub
from app.services.rooms import RoomService

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., min_length=10),
) -> None:
    """
    Authenticated real-time chat socket.

    Query params:
      - token: JWT access token from /api/auth/login

    Only approved users can connect.
    """
    try:
        user = await get_user_from_token_string(token)
    except Exception as exc:
        detail = getattr(exc, "detail", "Unauthorized")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(detail)[:120])
        return

    user_id = user.id
    user_name = user.display_name

    await manager.connect(websocket, user_id=user_id, user_name=user_name)

    user_channel = redis_pubsub.channel_for_user(user_id)
    await redis_pubsub.subscribe(user_channel)
    await redis_pubsub.subscribe(redis_pubsub.channel_global())

    await manager.send_json(
        websocket,
        {
            "event": "connected",
            "data": {
                "user_id": user_id,
                "user_name": user_name,
                "username": user.username,
                "role": user.role.value,
            },
        },
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = WSIncoming.model_validate_json(raw)
            except ValidationError:
                await manager.send_json(
                    websocket,
                    {"event": "error", "data": {"detail": "Invalid payload"}},
                )
                continue

            action = payload.action.lower().strip()

            if action == "ping":
                await manager.send_json(websocket, {"event": "pong", "data": {}})
                continue

            if action == "join":
                if not payload.room_id:
                    await manager.send_json(
                        websocket,
                        {"event": "error", "data": {"detail": "room_id required"}},
                    )
                    continue
                room_name = RoomService.normalize_name(payload.room_id)
                if not await RoomService.get_by_name(room_name):
                    await manager.send_json(
                        websocket,
                        {
                            "event": "error",
                            "data": {"detail": f"Room '{room_name}' does not exist"},
                        },
                    )
                    continue
                    
                if user.role != UserRole.ADMIN:
                    membership = await RoomService.get_membership(room_name, user_id)
                    if not membership or membership.status != RoomMembershipStatus.APPROVED:
                        await manager.send_json(
                            websocket,
                            {
                                "event": "error",
                                "data": {"detail": f"You are not approved to join '{room_name}'"},
                            },
                        )
                        continue

                # Leave previous rooms on this socket (single active room UX)
                meta = manager.get_meta(websocket) or {}
                for prev in list(meta.get("rooms", set())):
                    if prev != room_name:
                        await manager.leave_room(websocket, prev)
                        if not manager.is_user_in_room(user_id, prev):
                            await emit_room(
                                prev,
                                "user_left",
                                {
                                    "room_id": prev,
                                    "user_id": user_id,
                                    "user_name": user_name,
                                },
                            )

                was_in_room = manager.is_user_in_room(user_id, room_name)
                await manager.join_room(websocket, room_name)
                channel = redis_pubsub.channel_for_room(room_name)
                await redis_pubsub.subscribe(channel)
                await manager.send_json(
                    websocket,
                    {"event": "joined", "data": {"room_id": room_name}},
                )
                if not was_in_room:
                    await emit_room(
                        room_name,
                        "user_joined",
                        {
                            "room_id": room_name,
                            "user_id": user_id,
                            "user_name": user_name,
                        },
                    )
                continue

            if action == "leave":
                if not payload.room_id:
                    await manager.send_json(
                        websocket,
                        {"event": "error", "data": {"detail": "room_id required"}},
                    )
                    continue
                await manager.leave_room(websocket, payload.room_id)
                if not manager.is_user_in_room(user_id, payload.room_id):
                    await emit_room(
                        payload.room_id,
                        "user_left",
                        {
                            "room_id": payload.room_id,
                            "user_id": user_id,
                            "user_name": user_name,
                        },
                    )
                await manager.send_json(
                    websocket,
                    {"event": "left", "data": {"room_id": payload.room_id}},
                )
                continue

            if action == "send_message":
                if not payload.room_id or not payload.content:
                    await manager.send_json(
                        websocket,
                        {
                            "event": "error",
                            "data": {"detail": "room_id and content required"},
                        },
                    )
                    continue

                room_name = RoomService.normalize_name(payload.room_id)
                if not await RoomService.get_by_name(room_name):
                    await manager.send_json(
                        websocket,
                        {
                            "event": "error",
                            "data": {"detail": f"Room '{room_name}' does not exist"},
                        },
                    )
                    continue

                message = await MessageService.create_message(
                    room_id=room_name,
                    sender_id=user_id,
                    sender_name=user_name,
                    content=payload.content,
                    message_type=payload.message_type or MessageType.TEXT,
                    metadata=payload.metadata,
                )
                await emit_room(
                    room_name,
                    "message",
                    message.model_dump(mode="json"),
                )
                continue

            if action == "react":
                if not payload.message_id or not payload.emoji:
                    await manager.send_json(
                        websocket,
                        {
                            "event": "error",
                            "data": {"detail": "message_id and emoji required"},
                        },
                    )
                    continue
                try:
                    message = await MessageService.toggle_reaction(
                        message_id=payload.message_id,
                        emoji=payload.emoji,
                        user_id=user_id,
                        user_name=user_name,
                    )
                except Exception as exc:
                    detail = getattr(exc, "detail", "Failed to react")
                    await manager.send_json(
                        websocket,
                        {"event": "error", "data": {"detail": str(detail)}},
                    )
                    continue
                await emit_room(
                    message.room_id,
                    "reaction_updated",
                    message.model_dump(mode="json"),
                )
                continue

            await manager.send_json(
                websocket,
                {
                    "event": "error",
                    "data": {
                        "detail": f"Unknown action: {payload.action}",
                        "raw": json.loads(raw) if raw else {},
                    },
                },
            )
    except WebSocketDisconnect:
        meta = manager.get_meta(websocket) or {}
        rooms = list(meta.get("rooms", set()))
        await manager.disconnect(websocket)
        for room_id in rooms:
            if not manager.is_user_in_room(user_id, room_id):
                await emit_room(
                    room_id,
                    "user_left",
                    {
                        "room_id": room_id,
                        "user_id": user_id,
                        "user_name": user_name,
                    },
                )
