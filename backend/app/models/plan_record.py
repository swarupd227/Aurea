"""The plan of record (B-181): a household's plan as a produced artifact, one event at a time.

A baseline freezes the plan's figures as they stood and the assumption version they ran on; a review is a second
person's pass or fail on that baseline; a delivery says the reviewed baseline went to the client, from its evidence,
and raises its action items as tasks. Every row is added and never edited, and where a plan stands is worked out from
them (aurea_core/plan_record.py), never set.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

PLAN_EVENT_KINDS = ("baseline", "review", "delivery")
REVIEW_OUTCOMES = ("passed", "failed")
EVIDENCE_KINDS = ("document", "note")


class PlanRecordEvent(Base):
    """One baseline, review or delivery of a household's plan. Never edited."""

    __tablename__ = "plan_record_event"
    __table_args__ = (UniqueConstraint("household_id", "seq", name="uq_plan_record_event_seq"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    # 1, 2, 3... per household: the order of the record, and unique, so two people recording at once cannot both land.
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))
    # The plan this belongs to: 1 for the first, and a baseline recorded after a delivery starts the next.
    plan_version: Mapped[int] = mapped_column(Integer)
    # For a review or a delivery: the baseline it is about.
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan_record_event.id", ondelete="CASCADE"), nullable=True)
    # A review's outcome, and its findings (required when it failed).
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A delivery's evidence: a document or a meeting note.
    evidence_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The firm's day it happened on; created_at is the stored instant.
    happened_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    recorded_by_role: Mapped[str] = mapped_column(String(32))
    # A baseline: the figures frozen and the assumption version. A delivery: the baseline it delivers, copied as it was,
    # with its action items and the tasks they raised.
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
