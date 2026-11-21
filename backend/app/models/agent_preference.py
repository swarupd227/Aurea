"""What a person told an agent to remember, from a decision they took (B-204).

A preference is a person's, made from a proposal they decided, and it is visible and removable: a hold keeps one agent
from raising its finding for one subject until a date; a note goes on that agent's proposals for the subject so the
next person deciding sees it. Nothing here is worked out by a model.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AgentPreference(Base):
    __tablename__ = "agent_preference"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[str] = mapped_column(String(48), index=True)
    subject_type: Mapped[str] = mapped_column(String(24))
    subject_id: Mapped[uuid.UUID] = mapped_column(index=True)
    subject_label: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8))            # hold | note
    text: Mapped[str] = mapped_column(Text)                 # the person's own words
    until: Mapped[date | None] = mapped_column(Date, nullable=True)
    made_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    made_by_name: Mapped[str] = mapped_column(String(200))
    from_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
