"""Billing (L100 §9, L200 §2.1 Track A step 2 and its control "reconciliation to agreement
at first bill").

A `FeeArrangement` is what a client agreed to pay, and how it is billed: the fee schedule
confirmed under maker/checker, billing method and frequency, whether related accounts are
aggregated to reach a lower breakpoint. It used to live only on the onboarding case and
nothing read it after activation - which is why a bill could not be checked against
anything. It is created at activation from the case, and is the single source the billing
run reads.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ARRANGEMENT_SOURCES = ("onboarding", "seed", "manual")
RUN_STATUSES = ("proposed", "issued", "cancelled", "credited")
INVOICE_STATUSES = ("draft", "held", "issued", "debited", "credited", "void")


class FeeArrangement(Base):
    """The agreed fee terms for one mandate. At most one is active per mandate."""

    __tablename__ = "fee_arrangement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mandate.id", ondelete="CASCADE"), index=True
    )
    fee_schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_schedule.id", ondelete="RESTRICT")
    )
    billing_method: Mapped[str] = mapped_column(String(16))      # advance | arrears
    billing_frequency: Mapped[str] = mapped_column(String(16))   # monthly | quarterly | annually
    # Aggregate this mandate with the household's others on the same schedule, so the
    # breakpoint is reached on the combined balance.
    householding: Mapped[bool] = mapped_column(Boolean, default=False)

    effective_from: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | ended
    source: Mapped[str] = mapped_column(String(24), default="onboarding")

    # Maker/checker, carried over from the case: the confirming person differs from the setter.
    set_by: Mapped[str | None] = mapped_column(String(200))
    set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(200))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def is_confirmed(self) -> bool:
        return bool(self.confirmed_by and self.confirmed_at)


class BillingRun(Base):
    """One pass of the billing engine: a proposal until a person approves it.

    A run is proposed by the billing agent and issued only when an approver decides it, so
    "who authorised this month's fees" is a ledger entry, not an inference."""

    __tablename__ = "billing_run"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation.id", ondelete="SET NULL"), nullable=True
    )
    # The agent run that proposed it - how a run whose proposal was dismissed is recognised
    # and cancelled, rather than waiting for an approval that can no longer come.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    # Totals by currency, the mandates with no agreement, and arrangements already billed.
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invoice(Base):
    """A bill, or the credit note that offsets one.

    Once issued it is never edited or deleted: a correction is a credit note for the full
    amount, and a debit already posted to cash is offset by a matching credit - the same
    rule a settled trade follows (offset, never reversed)."""

    __tablename__ = "invoice"
    __table_args__ = (UniqueConstraint("firm_id", "invoice_number", name="uq_invoice_number"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_run.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="invoice")        # invoice | credit_note
    credit_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True
    )
    invoice_number: Mapped[str | None] = mapped_column(String(32))          # assigned when issued

    arrangement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fee_arrangement.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mandate.id", ondelete="SET NULL"), nullable=True, index=True
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    mandate_name: Mapped[str] = mapped_column(String(200))
    household_name: Mapped[str | None] = mapped_column(String(200))

    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(20, 2), default=0)        # negative on a credit note
    currency: Mapped[str] = mapped_column(String(8), default="NZD")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)

    # The full working: bands, accounts, valuation date and price age - what lets a bill
    # explain itself, and what a reconciliation re-derives and compares against.
    calculation: Mapped[dict] = mapped_column(JSON, default=dict)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    overrides: Mapped[list] = mapped_column(JSON, default=list)             # blocks knowingly accepted

    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str | None] = mapped_column(String(200))
    debit_status: Mapped[str | None] = mapped_column(String(24))            # posted | insufficient_cash | nothing_due
    debit_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    debited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_reason: Mapped[str | None] = mapped_column(Text)
