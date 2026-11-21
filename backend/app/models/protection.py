"""Protection cover on file - what would pay out if an earner died or could not work (L100 §5.4).

Every projection on the platform assumes the earner survives and keeps earning. A `ProtectionPolicy`
is one policy a client actually holds, recorded by a person from a document: a figure with no source
cannot be taken to a client. The absence of a row means no cover is on file with the firm, which is
not the same as the client having none, and nothing here says otherwise.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# life and tpd pay a lump sum; income_protection pays a yearly benefit while the insured cannot work,
# and long_term_care one while they need care.
POLICY_KINDS = ("life", "tpd", "income_protection", "long_term_care", "critical_illness", "personal_accident")
COVER_BASES = ("any_cause", "accident_only")


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
    annual_benefit: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)   # income, care
    # The age an income benefit stops being paid. None means to the insured's retirement age.
    benefit_to_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # How many years a care benefit pays. None means for as long as care lasts.
    benefit_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_premium: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="NZD")
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="in_force", index=True)  # in_force | ended
    # The document it came from - schedule, date and provider. Required.
    source_document: Mapped[str] = mapped_column(String(255))
    # India row 5. Whether it pays on any cause or only an accident; the policy it is a rider of; employer group
    # cover; and who is paid: the nominee as named, and a policy under the Married Women's Property Act.
    cover_basis: Mapped[str] = mapped_column(String(16), default="any_cause")
    rider_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protection_policy.id", ondelete="SET NULL"), nullable=True)
    group_cover: Mapped[bool] = mapped_column(Boolean, default=False)
    nominee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nominee_relationship: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mwp: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[str] = mapped_column(String(120), default="unknown")
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
