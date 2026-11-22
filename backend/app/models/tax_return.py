"""A filed tax return's facts, as a person recorded them from the document (B-183).

One row per return: the year, the filing status, who is on it (one person, or two on a joint return), AGI, taxable
income, the marginal and effective rate if the person read them off, carryforwards, the QBI deduction, each person's
Form 8606 basis and the state. Added and never edited: an amended return is recorded again, and the latest recorded
for the same people and year stands. Nothing here is worked out; OCR of a return is not done.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class TaxReturn(Base):
    __tablename__ = "tax_return"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_ids: Mapped[list] = mapped_column(JSON, default=list)            # who is on the return, as strings
    tax_year: Mapped[int] = mapped_column(Integer)
    filing_status: Mapped[str] = mapped_column(String(40))
    agi: Mapped[float] = mapped_column(Numeric(18, 2))
    taxable_income: Mapped[float] = mapped_column(Numeric(18, 2))
    marginal_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    effective_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    qbi_deduction: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    carryforwards: Mapped[list] = mapped_column(JSON, default=list)         # [{kind, amount}]
    basis_8606: Mapped[dict] = mapped_column(JSON, default=dict)            # person id -> nondeductible IRA basis
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    as_of: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
    # When it was recorded, to the microsecond, so an amended return recorded in the same transaction still stands:
    # `created_at` is the transaction's start in Postgres.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
