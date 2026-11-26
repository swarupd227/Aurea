"""What a person recorded for the India remainders: a manager's own staff, a PMS client's unit values, a verifier's letter and the debit of a
confirmed PMS fee line (B-303, B-283).

Added and never edited: a change is a new row and the latest for its subject stands. Nothing here is worked out or estimated.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ManagerAffiliation(Base):
    """A person recorded, from a document, as an employee or director of a fund manager (the AIF's lower minimum reads it)."""

    __tablename__ = "manager_affiliation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    manager: Mapped[str] = mapped_column(String(200))                       # the manager's name, as the document gives it
    role: Mapped[str] = mapped_column(String(12))                           # employee | director
    state: Mapped[str] = mapped_column(String(10), default="in_force")      # in_force | ended
    effective_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PmsUnitValue(Base):
    """One month-end net asset value per unit of a PMS client's portfolio, read off the manager's statement (B-283)."""

    __tablename__ = "pms_unit_value"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    nav_per_unit: Mapped[float] = mapped_column(Numeric(18, 6))
    units: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)   # units held that day, if the statement states them
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class PmsVerification(Base):
    """A verifier's letter that a strategy's reported returns were verified, with its date and scope (B-283)."""

    __tablename__ = "pms_verification"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(120))
    verifier: Mapped[str] = mapped_column(String(200))                      # APMI, PaRRVA or another, as the letter names it
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    letter_date: Mapped[date] = mapped_column(Date)
    scope: Mapped[str] = mapped_column(String(300))                         # what the letter says it covers
    state: Mapped[str] = mapped_column(String(10), default="verified")      # verified | withdrawn
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PmsFeeDebit(Base):
    """The debit of a confirmed PMS fee line from the client's own cash, made by a second person behind a Confirm card (B-283)."""

    __tablename__ = "pms_fee_debit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    fee_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pms_fee_line.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20))                         # posted | insufficient_cash
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    debited_by: Mapped[str] = mapped_column(String(200))
