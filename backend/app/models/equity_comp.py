"""Equity compensation and what a household plans to do about a concentrated position (L100 §5.2, §5.4).

A grant is recorded by a person from the plan statement. A plan is the adviser's and client's, from what they
agreed. Both are added, and a change is a new row.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

GRANT_KINDS = ("rsu", "iso", "nso", "espp", "restricted_stock")
OPTION_KINDS = ("iso", "nso")
PLAN_METHODS = ("staged_sale", "10b5_1", "exchange_fund", "collar", "gift", "hold")


class EquityGrant(Base):
    __tablename__ = "equity_grant"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    employer: Mapped[str] = mapped_column(String(200))
    symbol: Mapped[str | None] = mapped_column(String(32))
    units: Mapped[float] = mapped_column(Numeric(20, 4))
    strike: Mapped[float | None] = mapped_column(Numeric(20, 4))
    grant_date: Mapped[date] = mapped_column(Date)
    vesting: Mapped[list] = mapped_column(JSON, default=list)   # [{"on": "YYYY-MM-DD", "units": n}]
    expires_on: Mapped[date | None] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    ended_on: Mapped[date | None] = mapped_column(Date)
    ended_reason: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class ConcentrationPlan(Base):
    __tablename__ = "concentration_plan"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(20))
    target_weight: Mapped[float] = mapped_column(Numeric(5, 4))     # the share of the book it should come down to
    target_date: Mapped[date] = mapped_column(Date)
    rationale: Mapped[str] = mapped_column(Text)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
