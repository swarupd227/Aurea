"""A client's risk questionnaire, as answered on the day (L100 §5.2).

Each completed questionnaire is its own row and is never rewritten: this is the record a suitability
decision is examined against, so a later set of answers sits beside the earlier one, not over it. The
scores are stored with the version that produced them, so a past result stays what it was when the
scoring changes. What the mandate carries is a person's decision, in `Mandate.suitability`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RiskAssessment(Base):
    """One household's answers and what they indicate."""

    __tablename__ = "risk_assessment"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    scoring_version: Mapped[int] = mapped_column(Integer, default=1)
    # All three are empty when the answers on file are incomplete: nothing is scored from part of a form.
    tolerance: Mapped[str | None] = mapped_column(String(16), nullable=True)   # conservative | balanced | growth
    capacity: Mapped[str | None] = mapped_column(String(8), nullable=True)     # low | medium | high
    indicated: Mapped[str | None] = mapped_column(String(16), nullable=True)   # the lower of the two
    completed_by: Mapped[str] = mapped_column(String(120), default="unknown")
    completed_by_role: Mapped[str] = mapped_column(String(24), default="unknown")
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
