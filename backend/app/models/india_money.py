"""India money in and bank rules (B-388): a mandate's first debit, a recorded exception to the investor paying from their own bank,
what an Indian credit advice shows, and an inward remittance into a non-resident account. Each is recorded by a person from a
document, added and never edited, and none of it writes cash, places an order or presents a debit.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MandateDebit(Base):
    """What happened to a mandate's first debit, from the bank's or the fund's notice: confirmed or failed. The latest stands."""

    __tablename__ = "mandate_debit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    reference: Mapped[str] = mapped_column(String(60), index=True)    # the mandate's own reference
    outcome: Mapped[str] = mapped_column(String(10))                  # confirmed | failed
    debited_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class PayerException(Base):
    """Why a bank account that is not plainly the investor's may pay: a parent (or other relative) for a minor, an employer's payroll,
    or a bank that shows the investor's name in initials. `withdrawn` rows end one; the latest for the same account, kind, payer and
    last four digits stands."""

    __tablename__ = "payer_exception"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))                     # minor_relative | employer_payroll | name_confirmed
    payer_name: Mapped[str] = mapped_column(String(200))
    bank: Mapped[str] = mapped_column(String(120))
    last4: Mapped[str] = mapped_column(String(4))
    relationship: Mapped[str | None] = mapped_column(String(24), nullable=True)
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class IndiaCredit(Base):
    """What an Indian credit advice shows about one deposit (`incoming_deposit`): the rail, the bank's reference, who paid, and on what
    basis that payer was accepted."""

    __tablename__ = "india_credit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    deposit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incoming_deposit.id", ondelete="CASCADE"), index=True)
    rail: Mapped[str] = mapped_column(String(12))                     # upi | netbanking | rtgs | neft | cheque
    bank_reference: Mapped[str] = mapped_column(String(80), index=True)
    payer_name: Mapped[str] = mapped_column(String(200))
    payer_last4: Mapped[str] = mapped_column(String(4))
    basis: Mapped[str] = mapped_column(String(20))                    # investor | name_confirmed | minor_relative | employer_payroll
    exception_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payer_exception.id", ondelete="SET NULL"), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class InwardRemittance(Base):
    """Money that came from abroad into a non-resident investor's NRE or NRO bank account, with the bank certificate's reference."""

    __tablename__ = "inward_remittance"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    bank_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank_link.id", ondelete="SET NULL"), nullable=True)
    account_kind: Mapped[str] = mapped_column(String(4))              # nre | nro
    received_on: Mapped[date] = mapped_column(Date)
    amount_inr: Mapped[float] = mapped_column(Numeric(20, 2))
    foreign_amount: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    foreign_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    remitter_name: Mapped[str] = mapped_column(String(200))
    remitter_match: Mapped[str] = mapped_column(String(10))           # full | initials | none
    certificate_reference: Mapped[str] = mapped_column(String(80), index=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
