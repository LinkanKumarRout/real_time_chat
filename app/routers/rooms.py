from fastapi import APIRouter, Depends, status

from app.schemas import (
    RoomCreate,
    RoomListResponse,
    RoomOut,
    RoomRename,
    RoomThemeUpdate,
    UserOut,
    RoomMembershipOut,
    RoomMembershipListResponse,
)
from app.services.auth import get_current_user, require_admin
from app.services.realtime import emit_global, emit_room
from app.services.rooms import RoomService

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.get(
    "",
    response_model=RoomListResponse,
    summary="List all chat rooms",
)
async def list_rooms(_: UserOut = Depends(get_current_user)) -> RoomListResponse:
    rooms = await RoomService.list_rooms()
    return RoomListResponse(total=len(rooms), rooms=rooms)


@router.post(
    "",
    response_model=RoomOut,
    status_code=status.HTTP_201_CREATED,
    summary="Admin only: create a room",
)
async def create_room(
    payload: RoomCreate,
    admin: UserOut = Depends(require_admin),
) -> RoomOut:
    room = await RoomService.create_room(payload, admin_id=admin.id)
    await emit_global("room_created", room.model_dump(mode="json"))
    return room


@router.patch(
    "/{room_id}",
    response_model=RoomOut,
    summary="Admin only: rename a room",
)
async def rename_room(
    room_id: str,
    payload: RoomRename,
    _: UserOut = Depends(require_admin),
) -> RoomOut:
    room, old_name = await RoomService.rename_room(room_id, payload)
    data = {**room.model_dump(mode="json"), "old_name": old_name}
    await emit_global("room_renamed", data)
    if old_name != room.name:
        await emit_room(old_name, "room_renamed", data)
        await emit_room(room.name, "room_renamed", data)
    return room


@router.put(
    "/{room_id}/theme",
    response_model=RoomOut,
    summary="Admin only: set room theme (solid / image / preset)",
)
async def update_room_theme(
    room_id: str,
    payload: RoomThemeUpdate,
    _: UserOut = Depends(require_admin),
) -> RoomOut:
    room = await RoomService.set_theme(room_id, payload.theme)
    data = room.model_dump(mode="json")
    await emit_global("room_theme_updated", data)
    await emit_room(room.name, "room_theme_updated", data)
    return room


@router.delete(
    "/{room_id}/messages",
    summary="Admin only: clear all messages in a room",
)
async def clear_room_messages(
    room_id: str,
    _: UserOut = Depends(require_admin),
) -> dict[str, int | str]:
    from app.services.chat import MessageService

    room_doc = await RoomService.get_by_id(room_id)
    if not room_doc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Room not found")
    room_name = room_doc["name"]
    deleted = await MessageService.clear_room(room_name)
    await emit_room(
        room_name,
        "room_cleared",
        {"room_id": room_name, "deleted_count": deleted},
    )
    return {"room_id": room_name, "deleted_count": deleted}


@router.delete(
    "/{room_id}",
    response_model=RoomOut,
    summary="Admin only: delete a room (and its messages)",
)
async def delete_room(
    room_id: str,
    _: UserOut = Depends(require_admin),
) -> RoomOut:
    room = await RoomService.delete_room(room_id)
    data = room.model_dump(mode="json")
    await emit_global("room_deleted", data)
    await emit_room(room.name, "room_deleted", data)
    return room

@router.post(
    "/{room_id}/join",
    response_model=RoomMembershipOut,
    status_code=status.HTTP_201_CREATED,
    summary="User requests to join a room",
)
async def request_join(
    room_id: str,
    user: UserOut = Depends(get_current_user),
) -> RoomMembershipOut:
    membership = await RoomService.request_join(room_id, user.id)
    return membership


@router.post(
    "/{room_id}/members/{user_id}/approve",
    response_model=RoomMembershipOut,
    summary="Admin only: approve user to join a room",
)
async def approve_join(
    room_id: str,
    user_id: str,
    _: UserOut = Depends(require_admin),
) -> RoomMembershipOut:
    membership = await RoomService.approve_join(room_id, user_id)
    return membership


@router.delete(
    "/{room_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin only: remove user or reject join request",
)
async def remove_user(
    room_id: str,
    user_id: str,
    _: UserOut = Depends(require_admin),
) -> None:
    await RoomService.remove_user(room_id, user_id)
    # emit removed event so client can disconnect
    room_doc = await RoomService.get_by_id(room_id)
    if room_doc:
        await emit_room(
            room_doc["name"],
            "removed_from_room",
            {"room_id": room_doc["name"], "user_id": user_id}
        )


@router.get(
    "/{room_id}/members",
    response_model=RoomMembershipListResponse,
    summary="Admin only: list members and requests for a room",
)
async def list_members(
    room_id: str,
    _: UserOut = Depends(require_admin),
) -> RoomMembershipListResponse:
    memberships = await RoomService.list_memberships(room_id)
    return RoomMembershipListResponse(total=len(memberships), memberships=memberships)
