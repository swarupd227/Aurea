"""Declared valuations for private holdings (L100 §9).

A private fund cannot be quoted, so its value is what the manager last stated. Each statement
is kept - who recorded it, from which document, and what it replaced - because a valuation
that changes client balances and the fees charged on them must be traceable to evidence.
The price the rest of the platform reads is a `Price` row written alongside, marked
`source="declared_valuation"` (see `aurea_core/pricing.py`); this table is the audit trail.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ValuationStatement(Base):
    __tablename__ = "valuation_statement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    unit_value: Mapped[float] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(8), default="NZD")
    # What the valuation rests on - a statement name, date, manager. Required: no evidence, no value.
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    # The value this replaced, so a reviewer sees the move without reconstructing it.
    previous_value: Mapped[float | None] = mapped_column(Numeric(20, 6))
    previous_as_of: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)   # active | superseded
