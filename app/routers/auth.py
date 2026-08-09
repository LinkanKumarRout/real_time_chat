from fastapi import APIRouter, Depends, Query, status

from app.schemas import (
    TokenResponse,
    UserCreate,
    UserListResponse,
    UserLogin,
    UserOut,
    UserStatus,
)
from app.services.auth import AuthService, get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login (only approved users)",
)
async def login(payload: UserLogin) -> TokenResponse:
    return await AuthService.login(payload)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Current authenticated user",
)
async def me(user: UserOut = Depends(get_current_user)) -> UserOut:
    return user


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Admin only: create a user (starts as pending)",
)
async def register_user(
    payload: UserCreate,
    admin: UserOut = Depends(require_admin),
) -> UserOut:
    return await AuthService.create_user(payload, created_by=admin.id)


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="Admin only: list users",
)
async def list_users(
    status_filter: UserStatus | None = Query(None, alias="status"),
    _: UserOut = Depends(require_admin),
) -> UserListResponse:
    users = await AuthService.list_users(status_filter=status_filter)
    return UserListResponse(total=len(users), users=users)


@router.post(
    "/users/{user_id}/approve",
    response_model=UserOut,
    summary="Admin only: approve a pending user",
)
async def approve_user(
    user_id: str,
    admin: UserOut = Depends(require_admin),
) -> UserOut:
    return await AuthService.set_status(
        user_id,
        new_status=UserStatus.APPROVED,
        admin_id=admin.id,
    )


@router.post(
    "/users/{user_id}/reject",
    response_model=UserOut,
    summary="Admin only: reject a user",
)
async def reject_user(
    user_id: str,
    admin: UserOut = Depends(require_admin),
) -> UserOut:
    return await AuthService.set_status(
        user_id,
        new_status=UserStatus.REJECTED,
        admin_id=admin.id,
    )


@router.delete(
    "/users/{user_id}",
    response_model=UserOut,
    summary="Admin only: permanently delete a user",
)
async def delete_user(
    user_id: str,
    admin: UserOut = Depends(require_admin),
) -> UserOut:
    return await AuthService.delete_user(user_id, admin_id=admin.id)
