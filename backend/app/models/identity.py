"""Platform users (internal personas + Canvas clients) and auth."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "app_user"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(32), default=UserRole.ADVISER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str | None] = mapped_column(String(120))  # e.g. "Senior Adviser"

    # For advisers: links to the Person/clients they own (via relationship_edge).
    # For clients (Canvas): the Person they represent.
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), nullable=True
    )

    # TOTP-based MFA (set up via /api/auth/mfa/* endpoints)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class UserInviteToken(Base):
    """Short-lived tokens for account setup (invite) and password reset."""
    __tablename__ = "user_invite_token"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token_type: Mapped[str] = mapped_column(String(16), default="invite")  # "invite" | "reset"
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEvent(Base):
    """Lightweight append-only log for admin/access events (login, user lifecycle, password resets)."""
    __tablename__ = "audit_event"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str] = mapped_column(String(254))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict | None] = mapped_column(JSON)
