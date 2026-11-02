"""A tax document a person has seen arrive (L100 §5.9). The custodian or fund issues it; the firm tracks it."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TaxDocument(Base):
    __tablename__ = "tax_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    tax_year: Mapped[int] = mapped_column(Integer, index=True)
    issuer: Mapped[str] = mapped_column(String(200))    # the account or fund it is for
    received_on: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
