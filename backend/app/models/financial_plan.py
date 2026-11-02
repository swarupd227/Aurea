"""Income a household can count on in retirement, recorded by a person from a statement (L100 §5.4)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

STREAM_KINDS = ("social_security", "state_pension", "employer_pension", "annuity", "rental", "other")


class IncomeStream(Base):
    """One source of income. For Social Security `annual_amount` is the benefit at full retirement age, as the
    statement gives it, and `start_age` is when they plan to claim. A pension's other options sit beside it."""

    __tablename__ = "income_stream"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(200))
    annual_amount: Mapped[float] = mapped_column(Numeric(20, 2))
    start_age: Mapped[int] = mapped_column()
    inflation_linked: Mapped[bool] = mapped_column(Boolean, default=False)
    lump_sum: Mapped[float | None] = mapped_column(Numeric(20, 2))          # the pension's cash alternative
    joint_annual: Mapped[float | None] = mapped_column(Numeric(20, 2))      # its joint-life amount
    joint_survivor_share: Mapped[float | None] = mapped_column(Numeric(5, 4))
    source_document: Mapped[str] = mapped_column(String(300))
    as_of: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    ended_reason: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
