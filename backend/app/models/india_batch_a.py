"""What a person recorded for India's batch A items: overseas remittances (B-293), a debit mandate behind a SIP (B-295), a pledge of
units to a lender (B-296) and a gift between people (B-299). Each is added and never edited: a correction or an end is a new row,
and nothing here moves money, units or income. Savings schemes (B-296) are held-away lines with `attributes`, not a table of their own.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LrsRemittance(Base):
    """One remittance a person made abroad under the Liberalised Remittance Scheme, from the bank's advice (B-293).

    `amount_usd` is what the advice counts against the limit. `amount_inr` and `tcs` are as the advice shows them and are empty when it
    does not. A correction is a second row with `reverses_id` set, which takes the first out of every sum."""

    __tablename__ = "lrs_remittance"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    remitted_on: Mapped[date] = mapped_column(Date)
    amount_usd: Mapped[float] = mapped_column(Numeric(18, 2))
    amount_inr: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    purpose: Mapped[str] = mapped_column(String(24))
    bank: Mapped[str] = mapped_column(String(120))
    tcs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lrs_remittance.id", ondelete="SET NULL"), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class DebitMandate(Base):
    """A client's registered mandate to debit a verified bank account (NACH or UPI Autopay), from its registration (B-295).

    `reference` is the registration's own number (the UMRN for a NACH mandate), or the request's number before one is issued. `kind` is
    `requested`, `registered`, `failed` or `cancelled`: the latest row for a reference stands, so a change is another row and the first stays."""

    __tablename__ = "debit_mandate"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    bank_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank_link.id", ondelete="SET NULL"), nullable=True)
    reference: Mapped[str] = mapped_column(String(60), index=True)
    mandate_type: Mapped[str] = mapped_column(String(16))            # nach | upi_autopay | nach_paper
    kind: Mapped[str] = mapped_column(String(10), default="registered")   # requested | registered | failed | cancelled
    per_debit_limit: Mapped[float] = mapped_column(Numeric(18, 2))
    starts_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")     # active | paused | rejected (as the registration reports it)
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    # B-388: the day a request was made or a registration failed, and the request's reference a later registration replaces.
    event_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    supersedes_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)


class SecuritiesPledge(Base):
    """Units a lender holds a lien on, from the lender's confirmation (B-296). `kind` is `pledge` or `release`; a release is a second row that
    names the pledge it lifts, so the first stays. The units pledged now are the pledges less the releases."""

    __tablename__ = "securities_pledge"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="pledge")
    lender: Mapped[str] = mapped_column(String(160))
    units: Mapped[float] = mapped_column(Numeric(24, 6))
    marked_on: Mapped[date] = mapped_column(Date)
    pledge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("securities_pledge.id", ondelete="SET NULL"), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class TaxGift(Base):
    """A gift from one person to another in the household, from its document (B-299). The relationship is what the document or the adviser
    states: it is never inferred from the family graph. `instrument_id` names the asset when it is a security the receiver's accounts hold,
    so the income it earns afterwards can be read."""

    __tablename__ = "tax_gift"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    giver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    relationship: Mapped[str] = mapped_column(String(24))
    gifted_on: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(18, 2))
    consideration: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    description: Mapped[str] = mapped_column(String(200))
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instrument.id", ondelete="SET NULL"), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(24, 6), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_by: Mapped[str] = mapped_column(String(200))
