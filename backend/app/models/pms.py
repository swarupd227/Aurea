"""A Portfolio Manager's agreement with one client, as a person recorded it from the signed document (India row 8).

Added and never edited: a change of terms is a new row and the latest stands, so the record shows what was agreed on any
day. Fee terms are stored as stated; nothing here prices a fee. A term not stated is None and reads "not assessed".
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PmsAgreement(Base):
    __tablename__ = "pms_agreement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(120))                     # the investment approach's name
    kind: Mapped[str] = mapped_column(String(20))                          # discretionary | non_discretionary | advisory
    signed_on: Mapped[date] = mapped_column(Date)
    initial_corpus: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)   # INR put in at the start
    segregated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # held in the client's own demat, not pooled
    benchmarks: Mapped[list] = mapped_column(JSON, default=list)           # APMI benchmarks the strategy names
    # Fee terms as stated. fee_type: fixed | performance | hybrid. A performance fee names its hurdle and whether it is
    # charged only above the client's own high-water mark.
    fee_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    fixed_rate: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)        # share of AUM a year
    performance_rate: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)  # share of the profit above the hurdle
    hurdle_rate: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)       # yearly return before it applies
    high_water_mark: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    operating_expense_rate: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)  # excluding brokerage, a year
    exit_load: Mapped[list] = mapped_column(JSON, default=list)            # [y1, y2, y3] shares, as stated
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
