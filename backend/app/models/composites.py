"""GIPS composite reporting (L200-5 §2.5) — treated at practitioner depth, not deferred
to L300 (the correction the v2 gap analysis made against v1's own mis-scoping).

A `Composite` wraps a `ModelPortfolio` (the strategy) with the GIPS-specific facts a
model portfolio has no reason to carry on its own: documented inclusion criteria, a
creation date, and a seasoning period. Performance, dispersion, and the standard-deviation
figure are computed on demand in `aurea_core.composites`, not persisted as a time series —
snapshotting composite returns over time would need historical daily positions this
platform does not keep, and fabricating that history would be dishonest rather than
merely incomplete.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

COMPOSITE_STATUSES = ("active", "retired")


class Composite(Base):
    __tablename__ = "composite"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    model_portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_portfolio.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # "Documented inclusion criteria per strategy (objective, not judgmental)" — L200-5 §2.5.
    inclusion_criteria: Mapped[str] = mapped_column(Text)
    creation_date: Mapped[date] = mapped_column()
    # A new account is not composite-eligible on day one — GIPS calls this seasoning.
    seasoning_days: Mapped[int] = mapped_column(Integer, default=90)
    minimum_account_size: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
