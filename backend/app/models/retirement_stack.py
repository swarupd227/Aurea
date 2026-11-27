"""What a person records about India's retirement accounts beside the holding itself (B-395, `aurea_core/retirement_stack.py`).

The corpus of an EPF, PPF, NPS or SCSS account is a held-away line (`held_away.py`), recorded from its statement. What the line
does not carry sits here, each from a document: what goes in each month and who pays it (an employer's NPS contribution is one),
a rate an insurer quoted for an NPS annuity, and that a person is in the Unified Pension Scheme. Added and ended, never edited:
a newer statement ends the row it supersedes, which stays on file.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RetirementRecord(Base):
    __tablename__ = "retirement_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))            # contribution | annuity_rate | ups_membership
    scheme: Mapped[str] = mapped_column(String(8))           # epf | ppf | nps | ups
    payer: Mapped[str | None] = mapped_column(String(12), nullable=True)           # a contribution: own | employer
    monthly_amount: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    annuity_rate: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)  # percent a year, as quoted
    stated_on: Mapped[date] = mapped_column(Date)            # the statement's or the quote's date
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="current", index=True)  # current | ended
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
