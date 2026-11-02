"""Money coming in: the bank accounts it may come from, each deposit and its return, and standing instructions
revalidated (L200-1 §6.3). Each row is recorded by a person from a document."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BankLink(Base):
    __tablename__ = "bank_link"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    bank: Mapped[str] = mapped_column(String(120))
    last4: Mapped[str] = mapped_column(String(4))
    owner_name: Mapped[str] = mapped_column(String(200))
    third_party: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(16))           # micro_deposit | instant | voided_cheque
    status: Mapped[str] = mapped_column(String(12))           # verified | failed | revoked
    verified_on: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(String(300))
    callback_by: Mapped[str | None] = mapped_column(String(200))
    revoked_reason: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class IncomingDeposit(Base):
    __tablename__ = "incoming_deposit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(8))            # ach | wire | check
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    bank_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank_link.id", ondelete="SET NULL"), nullable=True)
    received_on: Mapped[date] = mapped_column(Date)
    hold_until: Mapped[date | None] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(String(300))
    returned_on: Mapped[date | None] = mapped_column(Date)
    return_code: Mapped[str | None] = mapped_column(String(4))
    return_evidence: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class InstructionCheck(Base):
    __tablename__ = "instruction_check"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    standing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("standing_instruction.id", ondelete="CASCADE"), index=True)
    checked_on: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(String(300))
    callback_by: Mapped[str | None] = mapped_column(String(200))
    recorded_by: Mapped[str] = mapped_column(String(200))
