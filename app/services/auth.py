from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from app.config import get_settings
from app.database import get_db
from app.models import serialize_doc, to_object_id, utcnow
from app.schemas import (
    TokenResponse,
    UserCreate,
    UserLogin,
    UserOut,
    UserRole,
    UserStatus,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(*, user_id: str, username: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def user_doc_to_out(doc: dict[str, Any]) -> UserOut:
    data = serialize_doc(doc)
    assert data is not None
    return UserOut(
        id=data["id"],
        username=data["username"],
        display_name=data["display_name"],
        role=UserRole(data["role"]),
        status=UserStatus(data["status"]),
        created_at=data["created_at"],
        approved_at=data.get("approved_at"),
    )


class AuthService:
    @staticmethod
    async def ensure_admin_seed() -> None:
        settings = get_settings()
        db = get_db()
        existing = await db.users.find_one({"username": settings.admin_username})
        if existing:
            return
        doc = {
            "username": settings.admin_username,
            "password_hash": hash_password(settings.admin_password),
            "display_name": settings.admin_display_name,
            "role": UserRole.ADMIN.value,
            "status": UserStatus.APPROVED.value,
            "created_at": utcnow(),
            "approved_at": utcnow(),
            "approved_by": None,
        }
        await db.users.insert_one(doc)

    @staticmethod
    async def create_user(payload: UserCreate, *, created_by: str) -> UserOut:
        db = get_db()
        existing = await db.users.find_one({"username": payload.username.lower()})
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already exists",
            )

        # Admin-created accounts start pending unless creating another admin seed path
        status_value = UserStatus.PENDING.value
        approved_at = None
        if payload.role == UserRole.ADMIN:
            # Extra admins still need explicit approval for safety
            status_value = UserStatus.PENDING.value

        doc = {
            "username": payload.username.lower(),
            "password_hash": hash_password(payload.password),
            "display_name": payload.display_name,
            "role": payload.role.value,
            "status": status_value,
            "created_at": utcnow(),
            "approved_at": approved_at,
            "approved_by": None,
            "created_by": created_by,
        }
        result = await db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
        return user_doc_to_out(doc)

    @staticmethod
    async def login(payload: UserLogin) -> TokenResponse:
        db = get_db()
        doc = await db.users.find_one({"username": payload.username.lower()})
        if not doc or not verify_password(payload.password, doc["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        status_value = UserStatus(doc["status"])
        if status_value == UserStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is pending admin approval",
            )
        if status_value == UserStatus.REJECTED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account was rejected by admin",
            )

        user = user_doc_to_out(doc)
        token = create_access_token(
            user_id=user.id,
            username=user.username,
            role=user.role.value,
        )
        return TokenResponse(access_token=token, user=user)

    @staticmethod
    async def get_user_by_id(user_id: str) -> dict[str, Any] | None:
        db = get_db()
        try:
            oid = to_object_id(user_id)
        except Exception:
            return None
        return await db.users.find_one({"_id": oid})

    @staticmethod
    async def list_users(
        *,
        status_filter: UserStatus | None = None,
    ) -> list[UserOut]:
        db = get_db()
        query: dict[str, Any] = {}
        if status_filter is not None:
            query["status"] = status_filter.value
        cursor = db.users.find(query).sort("created_at", -1)
        docs = await cursor.to_list(length=500)
        return [user_doc_to_out(d) for d in docs]

    @staticmethod
    async def set_status(
        user_id: str,
        *,
        new_status: UserStatus,
        admin_id: str,
    ) -> UserOut:
        db = get_db()
        try:
            oid = to_object_id(user_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc

        updates: dict[str, Any] = {"status": new_status.value}
        if new_status == UserStatus.APPROVED:
            updates["approved_at"] = utcnow()
            updates["approved_by"] = admin_id
        elif new_status == UserStatus.REJECTED:
            updates["approved_at"] = None
            updates["approved_by"] = admin_id
        elif new_status == UserStatus.PENDING:
            updates["approved_at"] = None
            updates["approved_by"] = None

        doc = await db.users.find_one_and_update(
            {"_id": oid},
            {"$set": updates},
            return_document=True,
        )
        if not doc:
            raise HTTPException(status_code=404, detail="User not found")
        return user_doc_to_out(doc)

    @staticmethod
    async def delete_user(user_id: str, *, admin_id: str) -> UserOut:
        db = get_db()
        if user_id == admin_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot delete your own account",
            )
        try:
            oid = to_object_id(user_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc

        doc = await db.users.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="User not found")

        if doc.get("role") == UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete an admin account",
            )

        user = user_doc_to_out(doc)
        await db.users.delete_one({"_id": oid})
        return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserOut:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    doc = await AuthService.get_user_by_id(user_id)
    if not doc:
        raise HTTPException(status_code=401, detail="User not found")

    user = user_doc_to_out(doc)
    if user.status != UserStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user.status.value}",
        )
    return user


async def require_admin(user: UserOut = Depends(get_current_user)) -> UserOut:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_user_from_token_string(token: str) -> UserOut:
    """Validate JWT for WebSocket connections."""
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    doc = await AuthService.get_user_by_id(user_id)
    if not doc:
        raise HTTPException(status_code=401, detail="User not found")
    user = user_doc_to_out(doc)
    if user.status != UserStatus.APPROVED:
        raise HTTPException(
            status_code=403,
            detail=f"Account is {user.status.value}",
        )
    return user
