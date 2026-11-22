"""A regulatory filing a person says was made, with its reference (R7). Added and never edited: one per obligation and
period, so the calendar shows what was filed and what is still owed."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ComplianceFiling(Base):
    __tablename__ = "compliance_filing"
    __table_args__ = (UniqueConstraint("firm_id", "obligation", "period", name="uq_compliance_filing_period"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    obligation: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[str] = mapped_column(String(24))        # the period it covers, as the calendar names it
    due_on: Mapped[date] = mapped_column(Date)
    filed_on: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(300))    # the acknowledgement, portal reference or where it is filed
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
