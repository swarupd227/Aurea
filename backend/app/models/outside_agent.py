"""A key a person gives an outside agent so it can call Astra's tools as them, through MCP (B-166).

The key is shown once and only its hash is kept. It names one person in one firm, so the agent reads what that person
may read and its state-changing calls wait on a Confirm card for that person. It expires, and the person or the firm's
administrator can revoke it at any time.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OutsideAgentAccess(Base):
    __tablename__ = "outside_agent_access"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))                    # the person's name for the agent
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(8))                 # the key's last four characters
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    # The conversation its confirmations are put on, made at its first state-changing call.
    thread_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
