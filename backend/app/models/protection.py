"""Protection cover on file - what would pay out if an earner died or could not work (L100 §5.4).

Every projection on the platform assumes the earner survives and keeps earning. A `ProtectionPolicy`
is one policy a client actually holds, recorded by a person from a document: a figure with no source
cannot be taken to a client. The absence of a row means no cover is on file with the firm, which is
not the same as the client having none, and nothing here says otherwise.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# life and tpd pay a lump sum; income_protection pays a yearly benefit while the insured cannot work.
POLICY_KINDS = ("life", "tpd", "income_protection")


class ProtectionPolicy(Base):
    """One policy on one person's life."""

    __tablename__ = "protection_policy"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    provider: Mapped[str] = mapped_column(String(120))
    policy_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sum_insured: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)      # life, tpd
    annual_benefit: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)   # income_protection
    # The age an income benefit stops being paid. None means to the insured's retirement age.
    benefit_to_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_premium: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="NZD")
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="in_force", index=True)  # in_force | ended
    # The document it came from - schedule, date and provider. Required.
    source_document: Mapped[str] = mapped_column(String(255))
    recorded_by: Mapped[str] = mapped_column(String(120), default="unknown")
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
