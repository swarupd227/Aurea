"""What a person records for India's tax planning (B-396): a return the firm or a document states, for the after-tax
comparison (`aurea_core/india_after_tax.py`), and that a taxpayer's regime review or harvest review was held
(`aurea_core/india_tax_year.py`). Both are added and never edited: a newer statement stands over an older one, and a
second review is another row.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StatedReturn(Base):
    __tablename__ = "india_stated_return"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    vehicle: Mapped[str] = mapped_column(String(24))         # deposit | debt_fund | arbitrage_fund | equity_fund
    name: Mapped[str] = mapped_column(String(200))           # the deposit or scheme, or the firm's own basis, as the document names it
    rate: Mapped[float] = mapped_column(Numeric(7, 4))       # percent a year, before tax, as stated
    stated_on: Mapped[date] = mapped_column(Date)            # the document's date
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class TaxReview(Base):
    __tablename__ = "india_tax_review"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(12))            # regime | harvest
    tax_year: Mapped[str] = mapped_column(String(12), index=True)
    reviewed_on: Mapped[date] = mapped_column(Date)
    covered: Mapped[str] = mapped_column(String(400))        # what was looked at and said, in the person's words
    source_document: Mapped[str] = mapped_column(String(300))
    figures: Mapped[dict] = mapped_column(JSON, default=dict)   # as they stood on the day it was recorded
    recorded_by: Mapped[str] = mapped_column(String(200))
