"""One consent a client gave or withdrew, with its evidence (L200-1 §3 step 5). Added and never edited: a withdrawal
is a new row, so the record shows what stood on any day."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

KINDS = ("e_delivery", "householding", "data_aggregation", "trusted_contact", "discretionary_authority", "fee_debit")
METHODS = ("e_signature", "signed_form", "portal", "email", "recorded_call")


class ConsentRecord(Base):
    __tablename__ = "consent_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), nullable=True, index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(12))      # granted | withdrawn
    given_by: Mapped[str] = mapped_column(String(200))
    given_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
