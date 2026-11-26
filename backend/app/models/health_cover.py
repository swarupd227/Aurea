"""Health cover and the savings policies an Indian household brings (B-397), as a person recorded them from the insurer's
own paper.

A `HealthPolicy` is one health policy: individual, family floater, super top-up (it pays above a deductible) or an
employer's group cover. It covers the people the schedule names, who need not be on the household's record (parents often
are not). Its limiting terms are held only as the document states them; a term not recorded is not a term the policy does
not have. A renewal is a new row that ends the one it replaces, so nothing is edited.

A `PolicyValueStatement` is what an insurer's statement says an endowment, money-back, whole-life or unit-linked policy is
worth: premiums paid, surrender value, paid-up value and the rest. It hangs off the life policy that holds the sum
assured, is added and never edited, and the latest statement stands. Nothing here is worked out or estimated.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

HEALTH_KINDS = ("individual", "family_floater", "super_top_up", "employer_group")
SAVINGS_PRODUCTS = ("endowment", "money_back", "whole_life", "unit_linked")
MATURITY_BASES = ("guaranteed", "illustrated")


class HealthPolicy(Base):
    __tablename__ = "health_policy"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(120))
    policy_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sum_insured: Mapped[float] = mapped_column(Numeric(20, 2))
    # What a super top-up pays above. None on every other kind.
    deductible: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    # [{person_id | null, name, relationship}] - who the schedule says is insured.
    members: Mapped[list] = mapped_column(JSON, default=list)
    room_rent_cap: Mapped[str | None] = mapped_column(String(200), nullable=True)
    co_payment_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    co_payment_terms: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sub_limits: Mapped[list] = mapped_column(JSON, default=list)        # [{item, limit}]
    waiting_periods: Mapped[list] = mapped_column(JSON, default=list)   # [{condition, months, ends_on}]
    covered_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    renewal_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    annual_premium: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    source_document: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(12), default="in_force")  # in_force | ended
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("health_policy.id", ondelete="SET NULL"), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    as_of: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PolicyValueStatement(Base):
    __tablename__ = "policy_value_statement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protection_policy.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(16))
    statement_date: Mapped[date] = mapped_column(Date)
    commenced_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    premiums_paid: Mapped[float] = mapped_column(Numeric(20, 2))
    annual_premium: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    premium_years_left: Mapped[int | None] = mapped_column(Integer, nullable=True)
    surrender_value: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    paid_up_value: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    fund_value: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    maturity_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    maturity_value: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    maturity_basis: Mapped[str | None] = mapped_column(String(12), nullable=True)  # guaranteed | illustrated
    charges_stated: Mapped[str | None] = mapped_column(String(300), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    source_document: Mapped[str] = mapped_column(String(255))
    recorded_by: Mapped[str] = mapped_column(String(200))
