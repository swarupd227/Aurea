"""Authentication endpoints."""
from __future__ import annotations

import uuid
from datetime import timezone

import jwt as _jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.security import (
    create_access_token, create_preauth_token, get_current_user,
    hash_password, verify_password,
)
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User, UserInviteToken
from app.models.tenant import Firm


async def _audit(
    db: AsyncSession, firm_id, actor: User | None,
    event_type: str, subject: str | None = None, detail: dict | None = None,
) -> None:
    db.add(AuditEvent(
        firm_id=firm_id,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "system",
        event_type=event_type, subject=subject, detail=detail,
    ))

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    user: dict | None = None
    mfa_required: bool = False
    mfa_token: str | None = None       # pre-auth JWT when mfa_required
    mfa_setup_required: bool = False   # advisory: ADMIN/COMPLIANCE should set up MFA


class LoginIn(BaseModel):
    email: str
    password: str


async def _authenticate(db: AsyncSession, email: str, password: str) -> User:
    user = (
        await db.execute(select(User).where(User.email == email.lower().strip()))
    ).scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is deactivated.")
    return user


def _user_dict(user: User, firm: Firm | None) -> dict:
    return {
        "id": str(user.id), "email": user.email, "full_name": user.full_name,
        "role": user.role, "title": user.title,
        "mfa_enabled": getattr(user, "mfa_enabled", False),
        "firm": {"id": str(firm.id), "name": firm.name, "slug": firm.slug,
                 "branding": firm.branding} if firm else None,
    }


def _mfa_setup_required(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.COMPLIANCE) and not getattr(user, "mfa_enabled", False)


async def _build_login_response(db: AsyncSession, user: User) -> TokenOut:
    """Return full JWT or pre-auth MFA challenge depending on user's MFA state."""
    if getattr(user, "mfa_enabled", False):
        return TokenOut(mfa_required=True, mfa_token=create_preauth_token(user))
    firm = await db.get(Firm, user.firm_id)
    await _audit(db, user.firm_id, user, "auth.login")
    return TokenOut(
        access_token=create_access_token(user),
        user=_user_dict(user, firm),
        mfa_setup_required=_mfa_setup_required(user),
    )


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await _authenticate(db, form.username, form.password)
    return await _build_login_response(db, user)


@router.post("/login-json")
async def login_json(body: LoginIn, db: AsyncSession = Depends(get_db)):
    user = await _authenticate(db, body.email, body.password)
    return await _build_login_response(db, user)


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    firm = await db.get(Firm, user.firm_id)
    return _user_dict(user, firm)


# ── MFA endpoints ─────────────────────────────────────────────────────────────

class MfaConfirmIn(BaseModel):
    code: str


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str


@router.post("/mfa/setup")
async def mfa_setup(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Generate a TOTP secret. Returns the secret and an otpauth:// URI for an authenticator app.
    Call /mfa/confirm with a valid code to activate."""
    import pyotp
    secret = pyotp.random_base32()
    user.mfa_secret = secret
    await db.flush()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name="Aurea")
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/confirm")
async def mfa_confirm(
    body: MfaConfirmIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify the TOTP code from the authenticator app and enable MFA."""
    import pyotp
    if not getattr(user, "mfa_secret", None):
        raise HTTPException(400, "Call /mfa/setup first to generate a secret.")
    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid verification code. Check your authenticator app.")
    user.mfa_enabled = True
    await _audit(db, user.firm_id, user, "auth.mfa_enabled")
    return {"ok": True, "mfa_enabled": True}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaVerifyIn, db: AsyncSession = Depends(get_db)):
    """Complete MFA login: exchange a pre-auth token + TOTP code for a full JWT."""
    try:
        payload = _jwt.decode(body.mfa_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if not payload.get("pre_auth"):
            raise HTTPException(400, "Invalid MFA session token.")
        user_id = payload.get("sub")
    except _jwt.PyJWTError:
        raise HTTPException(400, "MFA session expired or invalid. Please log in again.")

    user = await db.get(User, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(400, "Account not found or inactive.")
    if not getattr(user, "mfa_enabled", False) or not getattr(user, "mfa_secret", None):
        raise HTTPException(400, "MFA not configured for this account.")

    import pyotp
    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid verification code.")

    firm = await db.get(Firm, user.firm_id)
    await _audit(db, user.firm_id, user, "auth.login_mfa")
    return TokenOut(
        access_token=create_access_token(user),
        user=_user_dict(user, firm),
    )


@router.post("/mfa/disable")
async def mfa_disable(
    body: MfaConfirmIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable MFA — requires a valid TOTP code as confirmation."""
    if not getattr(user, "mfa_enabled", False) or not getattr(user, "mfa_secret", None):
        raise HTTPException(400, "MFA is not enabled on this account.")
    import pyotp
    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid verification code.")
    user.mfa_enabled = False
    user.mfa_secret = None
    await _audit(db, user.firm_id, user, "auth.mfa_disabled")
    return {"ok": True, "mfa_enabled": False}


# ── Invite / password ─────────────────────────────────────────────────────────

class AcceptInviteIn(BaseModel):
    token: str
    password: str


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteIn, db: AsyncSession = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    row = (await db.execute(
        select(UserInviteToken).where(UserInviteToken.token == body.token, ~UserInviteToken.used)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(400, "This link is invalid or has already been used. Ask your admin to resend.")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < utcnow():
        raise HTTPException(400, "This link has expired. Ask your admin to resend.")
    user = await db.get(User, row.user_id)
    if not user:
        raise HTTPException(400, "Account not found.")
    user.hashed_password = hash_password(body.password)
    user.is_active = True
    row.used = True
    firm = await db.get(Firm, user.firm_id)
    await _audit(db, user.firm_id, user, f"auth.{row.token_type}_accepted", subject=user.email)
    return TokenOut(
        access_token=create_access_token(user),
        user=_user_dict(user, firm),
    )


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    user.hashed_password = hash_password(body.new_password)
    await _audit(db, user.firm_id, user, "auth.password_changed")
    return {"ok": True}


@router.get("/demo-personas")
async def demo_personas(db: AsyncSession = Depends(get_db)):
    """Persona catalogue for the role switcher. Only exposed in the local/demo environment."""
    from app.core.config import settings
    from app.core.personas import DEMO_PERSONAS

    if settings.app_env not in ("local", "demo"):
        raise HTTPException(status_code=404, detail="Not available")

    existing = {
        u.email for u in (await db.execute(select(User))).scalars().all()
    }
    return [
        {"email": p["email"], "role": p["role"], "full_name": p["full_name"],
         "title": p["title"], "description": p["description"],
         "default_path": p["default_path"], "group": p["group"]}
        for p in DEMO_PERSONAS if p["email"] in existing
    ]
