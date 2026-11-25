"""What a person confirmed for India's polish row (B-261, B-262): a PMS performance fee carried onto a bill, and a minor's
account converted at majority. Each is added and never edited, from a Confirm card, and changes nothing else.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PmsFeeLine(Base):
    """A performance-fee estimate a person confirmed to carry onto the bill for one PMS client and period, as its own line.

    The amount is the estimate as it stood when it was confirmed, with its working. It is never added to the invoice's amount, so
    the fee cap does not read it; one line per mandate and period."""

    __tablename__ = "pms_fee_line"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(8))
    working: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class MajorityConversion(Base):
    """A person's record that a minor's account was converted to the adult's own at majority, from the document."""

    __tablename__ = "majority_conversion"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    converted_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
