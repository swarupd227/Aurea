"""The advice file (B-175): an adviser's own recommendations to a client, and what became of each.

Agent proposals have their ledger (`recommendation`); this is the person's. Both tables are added to and never edited:
a recommendation as it was made stays as it was made, and the client's decision changing, or the recommendation being
carried out, is a new `advice_outcome` row that supersedes the last, so the history is kept.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# What a recommendation is about. A small fixed set, so an implementation rate by type means the same thing everywhere.
ADVICE_CATEGORIES = ("protection", "investment", "retirement", "estate", "tax", "cash_debt", "other")
# Where a recommendation stands. open: the client has not decided; accepted and declined: the client's decision;
# implemented: it was carried out. Every one but open is recorded from its evidence.
ADVICE_STATUSES = ("open", "accepted", "declined", "implemented")
EVIDENCE_KINDS = ("document", "note")


class AdviceRecommendation(Base):
    """One recommendation an adviser made to a client, as it was made. Never edited."""

    __tablename__ = "advice_recommendation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    category: Mapped[str] = mapped_column(String(24))
    recommendation: Mapped[str] = mapped_column(Text)
    # The facts it rested on, as the adviser states them, the day those facts were as of, and the fingerprint of the
    # firm's assumption set when it was recorded (planning.assumption_version).
    facts: Mapped[str] = mapped_column(Text)
    data_as_of: Mapped[date] = mapped_column(Date)
    assumption_version: Mapped[str] = mapped_column(String(64))
    # [{"option": ..., "why_rejected": ...}], in the adviser's words.
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    recommended_on: Mapped[date] = mapped_column(Date)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    revisit_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorded_by_role: Mapped[str] = mapped_column(String(32))
    # The risk profile it rests on, as on file the day it was made (B-399, advice_practice.risk_basis). Null on a record
    # made before this was kept.
    risk_basis: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AdviceOutcome(Base):
    """Where a recommendation stands, from a date: the client's decision or its implementation, with the evidence.
    Added and never edited; the latest stands and the rest are its history."""

    __tablename__ = "advice_outcome"
    __table_args__ = (UniqueConstraint("recommendation_id", "seq", name="uq_advice_outcome_seq"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advice_recommendation.id", ondelete="CASCADE"), index=True)
    # 1 for where it stood when made, then 2, 3...: the order of the history, and unique, so two people recording the
    # same change at once cannot both land.
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    happened_on: Mapped[date] = mapped_column(Date)
    evidence_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A new revisit date, when the decision brings one ("declined; revisit at renewal").
    revisit_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorded_by_role: Mapped[str] = mapped_column(String(32))
