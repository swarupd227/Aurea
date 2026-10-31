"""A client's consent to a trade, on the record.

Under an Investment Adviser's licence a trade needs the client's specific and positive consent, on
every trade. An adviser's approval is not that consent, and every control elsewhere in the platform
is a staff control. So consent is its own record, bound to the exact orders it covers.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

CONSENT_STATUSES = ("awaiting", "given", "declined")
# How the client said it. "simulated" exists for demonstration firms and is never real consent.
CONSENT_CHANNELS = ("email", "recorded_call", "signed_document", "simulated")


class TradeConsent(Base):
    __tablename__ = "trade_consent"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation.id", ondelete="CASCADE"), unique=True, index=True
    )
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mandate.id", ondelete="SET NULL"), nullable=True, index=True
    )
    licence: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="awaiting", index=True)
    # What the consent covers: instrument, side and size. A changed order is a different trade.
    orders: Mapped[list] = mapped_column(JSON, default=list)
    order_digest: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(254))
    channel: Mapped[str | None] = mapped_column(String(24), nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
