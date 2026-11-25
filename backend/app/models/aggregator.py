"""Account Aggregator consents and consolidated account statements, as people recorded them (India row 9).

A consent artefact is the client's consented flow of financial information through an Account Aggregator: its purpose,
the kinds of information, the date range of the data, and when it lapses. It is added once and ended once (revoked), never
edited. An e-CAS import is a statement a person supplied, kept with the lines it was read into.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AaConsent(Base):
    __tablename__ = "aa_consent"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    artefact_id: Mapped[str] = mapped_column(String(80), index=True)       # the consent artefact's id at the aggregator
    aggregator: Mapped[str] = mapped_column(String(120))                   # the Account Aggregator that holds it
    purpose_code: Mapped[str] = mapped_column(String(8))
    fi_types: Mapped[list] = mapped_column(JSON, default=list)             # kinds of financial information consented to
    fetch_type: Mapped[str] = mapped_column(String(10), default="onetime")  # onetime | periodic
    data_from: Mapped[date] = mapped_column(Date)
    data_to: Mapped[date] = mapped_column(Date)
    consent_start: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))                     # the artefact as filed
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ended once, by a person, from the aggregator's notice or the client's own withdrawal.
    revoked_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_pull_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    pulls: Mapped[int] = mapped_column(Integer, default=0)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)   # simulated:<key> when simulated


class EcasStatement(Base):
    __tablename__ = "ecas_statement"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))                          # cams_kfintech | nsdl | cdsl
    statement_date: Mapped[date] = mapped_column(Date)
    period_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    lines: Mapped[list] = mapped_column(JSON, default=list)                # what was read, as given
    added: Mapped[int] = mapped_column(Integer, default=0)
    replaced: Mapped[int] = mapped_column(Integer, default=0)
