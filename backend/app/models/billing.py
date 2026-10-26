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

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ARRANGEMENT_SOURCES = ("onboarding", "seed", "manual")


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
