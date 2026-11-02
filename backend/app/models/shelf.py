"""The firm's product shelf: what its research team has decided about each product, and who may buy what
(L100 §5.7, §7). Both are recorded by a person, added and never edited; the latest row is the one in force.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

STATUSES = ("approved", "watch", "closed")
# Least to most: what a client must be certified as to buy a gated product.
INVESTOR_CLASSES = ("retail", "accredited", "qualified_purchaser")


class ProductDecision(Base):
    """One due-diligence decision on one product."""

    __tablename__ = "product_decision"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    document: Mapped[str] = mapped_column(String(300))
    reviewed_on: Mapped[date] = mapped_column(Date)
    next_review_on: Mapped[date | None] = mapped_column(Date)
    minimum_class: Mapped[str | None] = mapped_column(String(24))   # who may buy it; none means anyone
    family: Mapped[str | None] = mapped_column(String(120))         # share classes of one fund share a family
    share_class: Mapped[str | None] = mapped_column(String(24))
    expense_ratio: Mapped[float | None] = mapped_column(Numeric(6, 5))
    diversified: Mapped[bool] = mapped_column(Boolean, default=False)   # a fund of many issuers, so a position limit is not read against it
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_by_role: Mapped[str] = mapped_column(String(32))


class InvestorCertification(Base):
    """A person's standing to buy gated products, from the letter or form that shows it."""

    __tablename__ = "investor_certification"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    investor_class: Mapped[str] = mapped_column(String(24))
    as_of: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
