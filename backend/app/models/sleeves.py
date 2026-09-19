"""UMA sleeve architecture (L200-3 §6).

The custodian — and this platform's own `Holding` row — sees one flat account. `Sleeve`
and `SleeveHolding` are the logical partition L200-3 §6.1 describes on top of it: each
sleeve runs its own model, and `SleeveHolding` attributes a slice of an existing flat
`Holding`'s quantity/cost basis to the sleeve that logically owns it. The sleeve ledger
must reconcile upward to the flat custodial account — `aurea_core.sleeves.reconcile`
is that nightly check, made callable on demand.

Lot-level sleeve attribution (splitting a `TaxLot` itself across sleeves, the way real
direct-indexing engines do) is a deliberate L300-depth extension not built here — this
layer attributes at the holding level, which is enough to run netting (§6.2) and cash
equalization (§6.1) honestly, but not enough for sleeve-level lot-specific harvest
selection. Noted rather than silently assumed away."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SLEEVE_STATUSES = ("active", "closed")


class Sleeve(Base):
    """One logical partition of an account, running its own model."""

    __tablename__ = "sleeve"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_portfolio.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # This sleeve's share of the account, as a fraction — sleeves in one account should
    # sum to ~1.0; the reconciliation check (aurea_core.sleeves.reconcile) flags when they
    # do not, rather than the schema enforcing it, since a sleeve mid-wind-down legitimately
    # sits below its target for a while.
    target_weight: Mapped[float] = mapped_column(Numeric(6, 5), default=0)
    cash_target: Mapped[float] = mapped_column(Numeric(6, 5), default=0.02)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)


class SleeveHolding(Base):
    """Attribution of a slice of an existing flat `Holding` to one sleeve."""

    __tablename__ = "sleeve_holding"
    __table_args__ = (
        UniqueConstraint("sleeve_id", "holding_id", name="uq_sleeve_holding"),
    )

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    sleeve_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sleeve.id", ondelete="CASCADE"), index=True
    )
    holding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("holding.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    cost_basis: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
