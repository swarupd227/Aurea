"""A systematic investment plan: a client's standing instruction to buy a mutual fund each month (India row 4)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Sip(Base):
    """A monthly purchase of one fund in one account, for a fixed amount, from the client's signed registration."""

    __tablename__ = "sip"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    day_of_month: Mapped[int] = mapped_column(Integer)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    # Where the client's signed registration is on file. Empty only when simulated, on a demonstration firm.
    evidence_ref: Mapped[str | None] = mapped_column(String(300))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[str] = mapped_column(String(200))
    ended_on: Mapped[date | None] = mapped_column(Date)
    ended_by: Mapped[str | None] = mapped_column(String(200))
    end_reason: Mapped[str | None] = mapped_column(String(300))
    # The month (YYYY-MM) of the last instalment tried: one try a month, as a bank's SIP debit is.
    last_month: Mapped[str | None] = mapped_column(String(7))
    history: Mapped[list] = mapped_column(JSON, default=list)
