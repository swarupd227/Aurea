"""A household's emergency fund as a person recorded it (B-400, `aurea_core/planning_measures.py`): the target set, what
is set aside, where it sits and the statement it was read from. Added and never edited: the latest stands.

It adds no money to anything. The amount set aside is already on file as a statement line or a deposit, and `held_in`
says which; net worth, the goals and the retirement projection read those and never this."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EmergencyFund(Base):
    __tablename__ = "emergency_fund"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    target_amount: Mapped[float] = mapped_column(Numeric(20, 2))
    held_amount: Mapped[float] = mapped_column(Numeric(20, 2))
    held_in: Mapped[str | None] = mapped_column(String(200))
    statement_date: Mapped[date] = mapped_column(Date)
    # The day the household means to have it complete, and the months a build was planned over: only where a person said.
    complete_by: Mapped[date | None] = mapped_column(Date)
    build_months: Mapped[int | None] = mapped_column(Integer)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
