"""What the PMLA programme keeps (B-390, L200-1 India §5.3): a screening list change as it was received, a politically
exposed person's senior approval, and a report to FIU-IND a person says was filed. All added and never edited. None
of it is ever shown to a client.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ScreeningListUpdate(Base):
    """One change to a screening list the firm screens against, as a person recorded it from its notification."""

    __tablename__ = "screening_list_update"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    list_key: Mapped[str] = mapped_column(String(16))            # unsc | uapa | pep
    version: Mapped[str] = mapped_column(String(60))
    published_on: Mapped[date] = mapped_column(Date)             # the day the list changed: the backlog is aged from it
    entries: Mapped[list] = mapped_column(JSON, default=list)    # added or restated: name, father_name, dob, country, note
    removed: Mapped[list] = mapped_column(JSON, default=list)    # names taken off the list
    source_document: Mapped[str] = mapped_column(String(300))
    demonstration: Mapped[bool] = mapped_column(Boolean, default=False)   # invented entries: shown as invented
    recorded_by: Mapped[str] = mapped_column(String(200))


class PepApproval(Base):
    """Senior approval, or its refusal, for a relationship with a politically exposed person."""

    __tablename__ = "pep_approval"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), nullable=True, index=True)
    # A client with no onboarding case here: the person or entity the KYC record is about.
    holder_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    parties: Mapped[list] = mapped_column(JSON, default=list)    # the exposed people this decision covered, by name
    basis: Mapped[list] = mapped_column(JSON, default=list)      # how each was known to be one
    decision: Mapped[str] = mapped_column(String(16))            # approve | decline
    approver: Mapped[str] = mapped_column(String(200))           # the senior person who decided
    source_of_wealth: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(String(300))
    decided_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))


class FiuReport(Base):
    """A monthly report to FIU-IND that compliance says was filed, with the filing's own reference."""

    __tablename__ = "fiu_report"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))                # ctr | monthly
    period: Mapped[str] = mapped_column(String(7))               # YYYY-MM
    due_on: Mapped[date] = mapped_column(Date)
    filed_on: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(120))
    transactions: Mapped[int | None] = mapped_column(Integer)    # how many the report carried; 0 is a nil report
    principal_officer: Mapped[str] = mapped_column(String(200))  # as recorded on the day this was written
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(String(200))
