"""What compliance recorded about an Indian firm's governance (India row 14), each from a document.

Added and never edited: a change is a new row, and the latest for its subject stands. Nothing here is worked out and nothing here
says the firm is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class GovernancePolicy(Base):
    """A policy the firm keeps (grievance, cyber, business continuity, outsourcing, conflict of interest): its version, its owner, when it was
    approved and when it is next reviewed."""

    __tablename__ = "governance_policy"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    version: Mapped[str] = mapped_column(String(40))
    owner: Mapped[str] = mapped_column(String(200))
    approved_on: Mapped[date] = mapped_column(Date)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)      # the board, a committee or a person, as the document says
    next_review_on: Mapped[date] = mapped_column(Date)
    review_set: Mapped[bool] = mapped_column(default=False)      # the document set the date; otherwise a year after approval
    source_document: Mapped[str] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class CounselReview(Base):
    """Whether counsel read one law figure against its source, and what they found."""

    __tablename__ = "counsel_review"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    figure_key: Mapped[str] = mapped_column(String(80), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))                                   # counsel or the firm, as the opinion names them
    reviewed_on: Mapped[date] = mapped_column(Date)
    figure_as_of: Mapped[date] = mapped_column(Date)                                     # the figure's own as-of day when it was reviewed
    outcome: Mapped[str] = mapped_column(String(16))                                     # confirmed | corrected | not_confirmed
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
