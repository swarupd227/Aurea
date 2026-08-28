"""Orders and executions — the trade lifecycle.

Before this existed, an approved rebalance produced a dict claiming `executed: True`
while nothing moved: no order was stored, no fill happened, the holdings and tax lots
were untouched, and the `txn` table analytics reads was never written. The next drift
run therefore saw the same drift and proposed the same trades, forever.

These two tables are the spine that fixes it. An `Order` is the intent — created as a
DRAFT by the agent that proposed it, carrying `recommendation_id` so every order traces
back to the reasoning and the human approval behind it. An `Execution` is what actually
happened at a venue: one row per fill, so partial fills are the normal case rather than
a special one.

Money and quantities use Numeric, never float, and match the precision already used by
holding/tax_lot so a position reconciles exactly against the orders that built it."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import OrderSide, OrderStatus, OrderType, SettlementStatus, TimeInForce


class Order(Base):
    """An instruction to buy or sell, and the state it has reached."""

    __tablename__ = "order"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), index=True
    )
    # The mandate whose guardrails this order was checked against.
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mandate.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Provenance: the recommendation an adviser approved to authorise this order. Null
    # only for an order raised directly by a human rather than by an agent.
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation.id", ondelete="SET NULL"), nullable=True, index=True
    )

    side: Mapped[str] = mapped_column(String(8), default=OrderSide.BUY)
    order_type: Mapped[str] = mapped_column(String(12), default=OrderType.MARKET)
    time_in_force: Mapped[str] = mapped_column(String(8), default=TimeInForce.DAY)
    status: Mapped[str] = mapped_column(String(24), default=OrderStatus.DRAFT, index=True)

    quantity: Mapped[float] = mapped_column(Numeric(24, 6))
    limit_price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)

    # What the proposing agent expected, kept so slippage can be measured against reality.
    est_price: Mapped[float] = mapped_column(Numeric(20, 6), default=0)
    est_value: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    est_realised_gain: Mapped[float] = mapped_column(Numeric(20, 2), default=0)

    # Rolled up from executions as fills arrive.
    filled_quantity: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    avg_fill_price: Mapped[float] = mapped_column(Numeric(20, 6), default=0)
    fees: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    # Realised gain actually booked at settlement, from the lots the sale consumed.
    realised_gain: Mapped[float] = mapped_column(Numeric(20, 2), default=0)

    venue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    custodian: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    reason: Mapped[str] = mapped_column(String(512), default="")
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    settlement_status: Mapped[str] = mapped_column(
        String(16), default=SettlementStatus.UNSETTLED, index=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Append-only audit of every state change: who/what moved it, when, and why.
    history: Mapped[list] = mapped_column(JSON, default=list)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)

    executions: Mapped[list["Execution"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    @property
    def remaining_quantity(self) -> float:
        # Column defaults land at INSERT, so these are None on an unflushed order.
        return float(self.quantity or 0) - float(self.filled_quantity or 0)

    @property
    def is_terminal(self) -> bool:
        return str(self.status or OrderStatus.DRAFT) in {
            OrderStatus.FILLED, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED,
        }


class Execution(Base):
    """One fill against an order. An order may have many."""

    __tablename__ = "execution"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order.id", ondelete="CASCADE"), index=True
    )

    quantity: Mapped[float] = mapped_column(Numeric(24, 6))
    price: Mapped[float] = mapped_column(Numeric(20, 6))
    fees: Mapped[float] = mapped_column(Numeric(20, 2), default=0)

    venue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # True once this fill has been applied to holdings, tax lots and the txn ledger.
    settled: Mapped[bool] = mapped_column(Boolean, default=False)

    lineage: Mapped[dict] = mapped_column(JSON, default=dict)

    order: Mapped["Order"] = relationship(back_populates="executions")
