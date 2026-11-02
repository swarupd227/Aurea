"""A client leaving the firm, or dying, and what moved out of their accounts because of it (L100 §5.11).

Both are recorded by a person from a document. The custody connectors are mocks, so nothing here moves an
asset: it records what a custodian did. A transfer is added and never edited.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

EXIT_KINDS = ("departure", "death")


class ClientExit(Base):
    """One departure or estate settlement, and the book as it stood on its date."""

    __tablename__ = "client_exit"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    # The person who died. A departure is the household's or one mandate's.
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    event_date: Mapped[date] = mapped_column(Date)      # the date of death, or the day notice was given
    mandates: Mapped[list] = mapped_column(JSON, default=list)
    whole_household: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    receiving_institution: Mapped[str | None] = mapped_column(String(200))
    source_document: Mapped[str] = mapped_column(String(300))
    executor: Mapped[str | None] = mapped_column(String(200))
    executor_document: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)    # open | complete | cancelled
    book: Mapped[dict] = mapped_column(JSON, default=dict)                         # by account, on event_date
    opened_by: Mapped[str] = mapped_column(String(200))
    closed_by: Mapped[str | None] = mapped_column(String(200))
    closed_on: Mapped[date | None] = mapped_column(Date)
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class AccountTransfer(Base):
    """What left one account, to whom, on whose paper. Added and never edited."""

    __tablename__ = "account_transfer"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    exit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_exit.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))   # transfer_out | beneficiary_payout
    to_party: Mapped[str] = mapped_column(String(200))
    # Set when the money stays at the firm, in an account of its own.
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"), nullable=True)
    share: Mapped[float] = mapped_column(Numeric(7, 6))     # of the account as it stood on the exit's date
    transfer_date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(20, 2), default=0)     # of what has a real price, and the cash
    cash: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    positions: Mapped[list] = mapped_column(JSON, default=list)
    basis: Mapped[str] = mapped_column(String(16), default="carried")   # carried | stepped_up | left_firm
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
