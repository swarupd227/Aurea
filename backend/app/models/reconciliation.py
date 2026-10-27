"""Custodian reconciliation - what the custodian says an account holds against what the book says.

Until this existed the book was only ever compared with itself. Orders settle onto holdings,
cash and tax lots, but nothing asked the one party that holds the assets whether it agreed.
A `CustodyStatement` is the custodian's own statement of an account on one date - positions and,
when it carries one, cash. It is supplied by a person from a real statement; no feed is invented
for it (the custody connectors are mocks and say so). A `ReconBreak` is one disagreement found
when a statement is compared with the book as it stood on the statement's date.

A break is an exception that needs a judgement, so it keeps the evidence behind its likely cause
and what it would take to clear it. Those are codes and figures; the sentences are written by the
agent that speaks to the user.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

BREAK_KINDS = ("quantity", "missing_in_book", "missing_at_custodian", "unmapped_security", "cash")
BREAK_STATUSES = ("open", "explained", "escalated", "resolved", "dismissed", "cleared")
# The ones that no longer need anyone: fixed, set aside with a reason, or gone on a later statement.
CLOSED_STATUSES = ("resolved", "dismissed", "cleared")
CAUSES = ("settlement_timing", "fee_timing", "corporate_action_pending", "unmapped_security", "unexplained")
NEXT_STEPS = ("wait_for_next_statement", "post_entitlement", "map_instrument", "investigate")


class CustodyStatement(Base):
    """One account's statement from its custodian, as at one date."""

    __tablename__ = "custody_statement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    custodian: Mapped[str | None] = mapped_column(String(120), nullable=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    # [{"symbol": "AAPL", "quantity": 120.0}, ...] exactly as the custodian reports them.
    positions: Mapped[list] = mapped_column(JSON, default=list)
    # None when the statement carries no cash figure - then cash is not compared, not assumed zero.
    cash_balance: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="NZD")
    # The document it came from - name, date and custodian. Required: a figure with no source
    # cannot be taken to a custodian.
    source_document: Mapped[str] = mapped_column(String(255))
    imported_by: Mapped[str] = mapped_column(String(120), default="unknown")
    status: Mapped[str] = mapped_column(String(12), default="current", index=True)  # current | superseded
    # What comparing it with the book found, kept so the account's state can be read without a re-run.
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class ReconBreak(Base):
    """One disagreement between a custodian statement and the book."""

    __tablename__ = "recon_break"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    statement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("custody_statement.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instrument.id", ondelete="SET NULL"), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(48), nullable=True)

    # Units for a position, money for cash. difference = custodian - book, so a negative one
    # means the book holds more than the custodian says it does.
    book_value: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    custodian_value: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    difference: Mapped[float] = mapped_column(Numeric(24, 6), default=0)

    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    cause: Mapped[str] = mapped_column(String(32), default="unexplained")
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    next_step: Mapped[str] = mapped_column(String(32), default="investigate")
    # The records that make the likely cause the likely cause: orders, fee debits, entitlements.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)

    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Append-only: every change of status or figure, who or what made it, and when.
    history: Mapped[list] = mapped_column(JSON, default=list)
