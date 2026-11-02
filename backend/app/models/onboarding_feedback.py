"""A client's answer to the onboarding survey, as they gave it (L200-1 §8). Added and never edited."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OnboardingFeedback(Base):
    __tablename__ = "onboarding_feedback"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)                 # 0 to 10: how likely to recommend
    comment: Mapped[str | None] = mapped_column(String(1000))
    given_on: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(300))            # where the answer is: the survey export, the email
    recorded_by: Mapped[str] = mapped_column(String(200))
