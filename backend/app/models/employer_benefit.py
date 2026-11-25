"""What an employer's plan gives a person, recorded from the plan document (B-193): a retirement match, or a deferred
compensation election. Added and never edited; one that has ended is kept, with why. Nothing here is worked out or
estimated, and nothing here moves a plan figure.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

BENEFIT_KINDS = ("retirement_match", "deferred_compensation")
PAYOUT_FORMS = ("lump_sum", "installments")


class EmployerBenefit(Base):
    __tablename__ = "employer_benefit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    employer: Mapped[str] = mapped_column(String(200))
    # A match: the employer adds this share of what the person puts in (0.5 is fifty cents on the dollar), up to this
    # share of pay (0.06 is six percent), vesting as the plan document says.
    match_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    match_up_to: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    vesting: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # A deferred compensation election: a year's deferral, when and how it is paid out.
    annual_deferral: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    election_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payout_start_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payout_form: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payout_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    as_of: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
