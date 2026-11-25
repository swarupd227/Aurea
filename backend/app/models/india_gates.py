"""What a person recorded about an Indian client's standing and an adviser's fee terms (L100 India edition, B-291 and B-292).

Added and never edited: a change is a new row, and the latest for its subject stands. Nothing here is worked out from wealth and
nothing here says a firm or a fee is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AccreditedInvestor(Base):
    """A person's SEBI accredited-investor certificate as the document gives it (B-291), or the withdrawal of one.

    `state` is granted or withdrawn; a certificate past `valid_to` reads lapsed, which is "not accredited". The criteria are what the
    certificate states it rested on, laid out; they are never checked against the household's wealth here."""

    __tablename__ = "accredited_investor"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(10), default="granted")        # granted | withdrawn
    certificate_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agency: Mapped[str | None] = mapped_column(String(200), nullable=True)   # the accreditation agency, as the certificate names it
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    criteria: Mapped[list | None] = mapped_column(JSON, nullable=True)       # what the certificate says it rested on, as lines of text
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)           # for a withdrawal
    recorded_by: Mapped[str] = mapped_column(String(200))


class AdviserFeeRecord(Base):
    """One recorded term of an Investment Adviser's fees for a household's family (B-292).

    kind: `advance` (the agreement lets fees be collected up to `advance_months` ahead), `mode` (the mode the adviser elected, `aua` or
    `fixed`, from `effective_on`; a second row is a switch and the earlier stays), or `second_opinion` (an AUA-based fee on assets held under
    a distribution arrangement: `assets`, `rate`, the `fee` charged and `charged_on`)."""

    __tablename__ = "adviser_fee_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    effective_on: Mapped[date] = mapped_column(Date)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    advance_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assets: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    rate: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    fee: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
