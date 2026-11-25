"""What a person recorded for the Indian tax engine: a capital loss to carry forward, and an equity's value on 31 Jan 2018 (India row 11).

Both are added and never edited: the latest record for the same taxpayer, year and kind (or the same instrument) stands, so
the history shows what was believed on any day. Nothing here is worked out from a trade or fetched from a feed: a loss is
what a filed return states, and the 2018 value is the exchange's price as a person read it from its published record.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TaxLossEntry(Base):
    __tablename__ = "tax_loss_entry"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    tax_year: Mapped[str] = mapped_column(String(9))                       # the year the loss arose, "2023-24"
    kind: Mapped[str] = mapped_column(String(12))                          # short_term | long_term
    amount: Mapped[float] = mapped_column(Numeric(16, 2))                  # as the filed return states it, INR
    used: Mapped[float] = mapped_column(Numeric(16, 2), default=0)         # already set off in later years, as recorded
    return_filed_on: Mapped[date | None] = mapped_column(Date, nullable=True)   # None: no return on file for that year
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class GrandfatherValue(Base):
    __tablename__ = "grandfather_value"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    fmv_per_unit: Mapped[float] = mapped_column(Numeric(20, 6))            # the highest price on 31 Jan 2018 on the exchange, INR
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
