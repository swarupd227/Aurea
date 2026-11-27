"""The sample audit of advice files (B-399): which files compliance drew, and what it found on each.

Both tables are added to and never edited. A draw keeps the files it drew; a result is one per file per draw, with each
finding in the reviewer's words and what the record showed when it was reviewed (aurea_core/advice_practice.py).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AdviceAudit(Base):
    """One sample of advice files, drawn for review."""

    __tablename__ = "advice_audit"
    __table_args__ = (UniqueConstraint("firm_id", "seq", name="uq_advice_audit_seq"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    drawn_on: Mapped[date] = mapped_column(Date)
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    population: Mapped[int] = mapped_column(Integer)
    # The recommendation ids drawn, in the order drawn.
    sample: Mapped[list] = mapped_column(JSON, default=list)
    drawn_by: Mapped[str] = mapped_column(String(200))


class AdviceAuditResult(Base):
    """What compliance found on one drawn file. Added and never edited."""

    __tablename__ = "advice_audit_result"
    __table_args__ = (UniqueConstraint("audit_id", "recommendation_id", name="uq_advice_audit_result"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advice_audit.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advice_recommendation.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(8))                    # "met" or "finding"
    # [{"item": ..., "words": ...}], in the reviewer's words; empty when the file met the standard.
    findings: Mapped[list] = mapped_column(JSON, default=list)
    # What the record showed on the day it was reviewed.
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
