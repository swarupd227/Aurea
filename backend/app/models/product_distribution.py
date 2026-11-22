"""Who a product is for, and who it was offered to (docs/03_Architecture/product-distribution.md).

A target market is read by the research team from the product's own document and recorded by a person; a
distribution sign-off is compliance's, from someone other than that person; a pitch is the adviser's record of
offering a product to a client and what came of it. All three are added and never edited: the latest row is
the one in force.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

OBJECTIVES = ("growth", "income", "preservation", "diversification")
COMPLEXITY = ("simple", "complex")
LIQUIDITY = ("daily", "weekly", "monthly", "quarterly", "locked")
DISTRIBUTION = ("advised_only", "execution_also")
CONFLICTS = ("none", "proprietary", "third_party_payment")
# Who a product is not for, each read from a fact on file; one that is not on file is a fact to record, never a no.
NEGATIVE = ("low_capacity", "conservative_profile", "aged_over_75", "needs_access_within_lock")
# What a pitch can come to. The first three are open: the client is still being spoken to about it.
PITCH_OPEN = ("to_discuss", "discussed", "interested")
PITCH_STATUSES = (*PITCH_OPEN, "declined", "not_suitable", "bought", "withdrawn")


class ProductTargetMarket(Base):
    """One version of who a product is for, from its document."""

    __tablename__ = "product_target_market"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    risk_profiles: Mapped[list] = mapped_column(JSON, default=list)      # conservative, balanced, growth
    min_capacity: Mapped[str | None] = mapped_column(String(8))          # low, medium, high
    min_horizon_years: Mapped[int | None] = mapped_column(Integer)
    min_investment: Mapped[float | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    complexity: Mapped[str] = mapped_column(String(8), default="simple")
    liquidity: Mapped[str] = mapped_column(String(10), default="daily")
    lock_years: Mapped[int | None] = mapped_column(Integer)
    markets: Mapped[list] = mapped_column(JSON, default=list)            # empty: every market the firm serves
    mandate_types: Mapped[list] = mapped_column(JSON, default=list)      # empty: any
    negative_market: Mapped[list] = mapped_column(JSON, default=list)
    distribution: Mapped[str] = mapped_column(String(16), default="advised_only")
    conflict: Mapped[str] = mapped_column(String(24), default="none")
    disclosure: Mapped[str | None] = mapped_column(Text)                 # the words every message must carry if conflicted
    document: Mapped[str] = mapped_column(String(300))
    as_of: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorded_by_role: Mapped[str] = mapped_column(String(32))


class ProductDistributionSignoff(Base):
    """Compliance's sign-off that a complex or conflicted product may be offered, for one target-market version."""

    __tablename__ = "product_distribution_signoff"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    target_market_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_target_market.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    decided_on: Mapped[date] = mapped_column(Date)
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_by_role: Mapped[str] = mapped_column(String(32))


class ProductPitch(Base):
    """One step in offering a product to a household: the fit it rested on, and what came of it."""

    __tablename__ = "product_pitch"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandate.id", ondelete="SET NULL"), nullable=True)
    adviser_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    target_market_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_target_market.id", ondelete="SET NULL"), nullable=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recommendation.id", ondelete="SET NULL"), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    fit: Mapped[dict] = mapped_column(JSON, default=dict)                # the fit lines it rested on, as of `as_of`
    as_of: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
