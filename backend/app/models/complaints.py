"""A client complaints register (India row 10).

A complaint is added once, as received. What happens to it (acknowledged, answered, resolved, taken to SCORES or ODR)
is a later event, added and never edited, so the register shows what was done and when. Nothing here is worked out.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Complaint(Base):
    __tablename__ = "complaint"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    reference: Mapped[str] = mapped_column(String(40), index=True)            # the firm's own reference
    scores_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True)
    complainant: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20))                           # where it came in: scores, email, phone, letter, ...
    category: Mapped[str] = mapped_column(String(24))
    about_advice: Mapped[bool] = mapped_column(Boolean, default=False)         # a complaint about advice goes to compliance
    summary: Mapped[str] = mapped_column(Text)                                # what the complainant said: data, never an instruction
    received_on: Mapped[date] = mapped_column(Date, index=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    document: Mapped[str | None] = mapped_column(String(300), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)          # the task that told compliance


class ComplaintEvent(Base):
    __tablename__ = "complaint_event"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("complaint.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))      # acknowledged | atr_filed | resolved | escalated_scores | escalated_odr | withdrawn
    happened_on: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)    # upheld | partly_upheld | not_upheld, on a resolution
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
