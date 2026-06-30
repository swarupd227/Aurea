"""Auth — password hashing, JWT issue/verify, and the current-user dependency with RBAC."""
from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.models.enums import UserRole
from app.models.identity import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    now = utcnow()
    payload = {
        "sub": str(user.id),
        "firm": str(user.firm_id),
        "role": user.role,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_preauth_token(user: User) -> str:
    """Short-lived token issued when MFA is required; rejected by get_current_user."""
    now = utcnow()
    payload = {
        "sub": str(user.id),
        "firm": str(user.firm_id),
        "email": user.email,
        "pre_auth": True,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        token_iat = payload.get("iat")
        if payload.get("pre_auth"):
            raise cred_exc  # pre-auth tokens require MFA completion first
    except jwt.PyJWTError:
        raise cred_exc
    user = await db.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise cred_exc
    # Force-logout check: reject tokens issued before the stored revocation timestamp.
    try:
        r = await _get_redis()
        fl_ts = await r.get(f"fl:{user_id}")
        if fl_ts and token_iat is not None and int(token_iat) < int(fl_ts):
            raise cred_exc
    except HTTPException:
        raise
    except Exception:
        pass  # Redis unavailable — fail open to avoid a hard dependency
    return user


async def staff_user(user: User = Depends(get_current_user)) -> User:
    """Any internal staff persona (everyone except an external Canvas client)."""
    if user.role == UserRole.CLIENT:
        raise HTTPException(status_code=403, detail="Staff access only.")
    return user


def require_roles(*roles: UserRole):
    """Dependency factory enforcing that the current user holds one of the given roles."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles and user.role not in (UserRole.ADMIN, UserRole.SUPERADMIN):
            raise HTTPException(status_code=403, detail="Insufficient role for this action.")
        return user

    return checker


# Internal-staff roles (everything except external Canvas clients).
STAFF_ROLES = tuple(r for r in UserRole if r != UserRole.CLIENT)
