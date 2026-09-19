"""Corporate actions as an operations discipline (L200-4 §5).

L200-4's six-step event lifecycle — capture, entitlement, election management, posting &
payment, verification, basis & data downstream — condensed to two governed rows:
`CorporateAction` (the event itself, one per instrument per announcement) and
`CorporateActionEntitlement` (what one account is owed, elected, and was posted, with the
cost-basis adjustment that must flow to its tax lots). Nothing here executes a trade — a
posted entitlement adjusts `Holding`/`TaxLot` directly, the way a dividend or split
economically is, not the way a discretionary buy/sell is."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ACTION_TYPES = (
    "cash_dividend", "stock_dividend", "split", "reverse_split", "merger",
    "spin_off", "rights_issue", "tender_offer", "return_of_capital",
)
ACTION_STATUSES = ("announced", "election_open", "election_closed", "posted", "verified")
ENTITLEMENT_STATUSES = ("pending", "elected", "posted", "verified", "claim_raised")


class CorporateAction(Base):
    """One announced event on one instrument (L200-4 §5.1 steps 1-2)."""

    __tablename__ = "corporate_action"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(16), default="announced", index=True)
    # Voluntary events (mergers with elections, rights issues, tender offers) need a client/
    # PM decision; mandatory events (splits, cash dividends) do not — §5.1 step 3.
    is_voluntary: Mapped[bool] = mapped_column(Boolean, default=False)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payable_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # §5.1 step 3: publish client/PM deadlines earlier than the custodian's own deadline.
    election_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Event-specific terms: ratio (splits), cash-per-share, election options + defaults
    # (mergers/tenders), spin-off allocation basis, withholding rate, source vendor lineage.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CorporateActionEntitlement(Base):
    """What one account is owed, elected, and was posted for a given event.

    (L200-4 §5.1 steps 2-6: entitlement, election, posting, verification, basis-adjustment.)
    """

    __tablename__ = "corporate_action_entitlement"
    __table_args__ = (
        UniqueConstraint("corporate_action_id", "account_id", name="uq_ca_entitlement_account"),
    )

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    corporate_action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporate_action.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    holding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("holding.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # Position as of record date — the entitlement computation's input (§5.1 step 2).
    shares_entitled: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    # Voluntary-event choice, e.g. "cash" | "stock" | "default" (§5.1 step 3).
    election_choice: Mapped[str | None] = mapped_column(String(32), nullable=True)
    election_made_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    election_made_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # What was actually posted on pay date (§5.1 step 4).
    cash_amount: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    share_amount: Mapped[float | None] = mapped_column(Numeric(24, 6), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reconcile expected vs. received; a shortfall opens a counterparty claim (§5.1 step 5).
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The basis adjustment this event must apply to the account's tax lots — split ratio,
    # spin-off fair-value allocation %, return-of-capital basis reduction (§5.1 step 6).
    # Recorded here as the audit trail even once TaxLot rows are actually adjusted.
    cost_basis_adjustment: Mapped[dict] = mapped_column(JSON, default=dict)
