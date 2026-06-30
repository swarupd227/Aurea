"""Advise & engage models (spec §8, Table 9): meetings, tasks and client reports.

A meeting carries the prepared brief (Meeting Prep), the transcript and structured notes
(Meeting Companion). Action items become Tasks and proposed objectives become Goals on
adviser approval. Research & Reporting produces a ClientReport that is reviewed before it is
client-ready."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import MeetingStatus, ReportStatus, TaskStatus


class Meeting(Base):
    __tablename__ = "meeting"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[MeetingStatus] = mapped_column(String(16), default=MeetingStatus.SCHEDULED, index=True)
    # The prepared brief (Meeting Prep output) — structured sections.
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    # Raw transcript fed to the Meeting Companion.
    transcript: Mapped[str | None] = mapped_column(Text)
    # Structured notes (summary, decisions, sentiment) from the Companion.
    notes: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(Base):
    __tablename__ = "task"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(280))
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(String(12), default=TaskStatus.OPEN, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(48), default="agent")  # which agent proposed it
    subject_label: Mapped[str | None] = mapped_column(String(200))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )


class ClientReport(Base):
    __tablename__ = "client_report"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    period: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ReportStatus] = mapped_column(String(16), default=ReportStatus.DRAFT, index=True)
    # Structured sections: [{"heading":..., "body":...}, ...] + data blocks.
    sections: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
