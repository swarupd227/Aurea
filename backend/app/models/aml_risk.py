"""A customer risk rating as it was made (L200-1 §5.3). Added and never edited: a new rating is a new row."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AmlRiskRating(Base):
    __tablename__ = "aml_risk_rating"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(16))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)        # what the rating was made from, as recorded
    factors: Mapped[list] = mapped_column(JSON, default=list)      # each factor's points and why
    score: Mapped[float] = mapped_column(Numeric(5, 1))
    model_band: Mapped[str] = mapped_column(String(8))
    band: Mapped[str] = mapped_column(String(8))                   # what applies: the model's, or the override
    override_reason: Mapped[str | None] = mapped_column(Text)
    paths: Mapped[list] = mapped_column(JSON, default=list)        # designed paths that apply, each with what it requires
    review_months: Mapped[int] = mapped_column(Integer)
    next_review_on: Mapped[date] = mapped_column(Date)
    rated_by: Mapped[str] = mapped_column(String(200))
    rated_by_role: Mapped[str] = mapped_column(String(32))
