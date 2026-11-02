"""Money leaving a client's account, and money held back from being invested (L100 §5.3, §5.7, §5.8)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

CASH_KINDS = ("withdrawal", "rmd", "standing")
FREQUENCIES = {"monthly": 1, "quarterly": 3, "annual": 12}


class StandingInstruction(Base):
    """A pre-authorised payment from an account (an SLOA), from the signed form."""

    __tablename__ = "standing_instruction"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    payee: Mapped[str] = mapped_column(String(200))
    third_party: Mapped[bool] = mapped_column(Boolean, default=False)
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    frequency: Mapped[str] = mapped_column(String(12))
    first_due: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    ended_on: Mapped[date | None] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))


class CashRequest(Base):
    """One payment out, from the request to the custodian's confirmation that it went."""

    __tablename__ = "cash_request"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    payee: Mapped[str] = mapped_column(String(200))
    third_party: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="requested", index=True)  # requested | paid | cancelled
    requested_by: Mapped[str] = mapped_column(String(200))
    standing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("standing_instruction.id", ondelete="SET NULL"), nullable=True)
    callback_number: Mapped[str | None] = mapped_column(String(48))
    callback_by: Mapped[str | None] = mapped_column(String(200))
    paid_on: Mapped[date | None] = mapped_column(Date)
    paid_document: Mapped[str | None] = mapped_column(String(300))
    paid_by: Mapped[str | None] = mapped_column(String(200))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class PhaseInPlan(Base):
    """New money going in by stages. What has not reached its date is held back from every purchase."""

    __tablename__ = "phase_in_plan"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    tranches: Mapped[int] = mapped_column(Integer)
    every_months: Mapped[int] = mapped_column(Integer)
    start_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(300))
    ended_on: Mapped[date | None] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
