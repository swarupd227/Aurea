"""What a household owns outside the book, what it owes and what it spends (L100 §5.4).

The managed book is only part of a client's wealth, and a debt is invisible to everything that reads it.
A `BalanceItem` is one asset, liability or expense, recorded by a person from a document: a figure with no
source cannot be taken to a client. The absence of a row means nothing is on file with the firm, which
is not the same as the client having none, and nothing here says otherwise.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ASSET_KINDS = ("held_away_investment", "pension", "cash", "property", "business", "other_asset")
LIABILITY_KINDS = ("mortgage", "personal_loan", "credit_card", "student_loan", "tax_owed", "other_debt")
EXPENSE_KINDS = ("essential_spending", "discretionary_spending")


def side_of(kind: str) -> str | None:
    return ("asset" if kind in ASSET_KINDS else "liability" if kind in LIABILITY_KINDS
            else "expense" if kind in EXPENSE_KINDS else None)


class BalanceItem(Base):
    """One asset, debt or yearly expense of a household. Added and ended, never edited: a new figure
    replaces the old one and the old one stays on file."""

    __tablename__ = "balance_item"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    # Whose it is. None means the household's jointly.
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    side: Mapped[str] = mapped_column(String(12), index=True)  # asset | liability | expense
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))             # the institution, lender or description
    # The value, the balance owed, or the amount a year.
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="NZD")
    annual_payment: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)  # liability
    interest_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)    # liability, a fraction
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)                    # liability
    as_of: Mapped[date] = mapped_column(Date)   # the day the figure was true, not the day it was typed
    status: Mapped[str] = mapped_column(String(12), default="current", index=True)  # current | ended
    # The document it came from - name, date and issuer. Required.
    source_document: Mapped[str] = mapped_column(String(255))
    recorded_by: Mapped[str] = mapped_column(String(120), default="unknown")
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("balance_item.id", ondelete="SET NULL"), nullable=True)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
