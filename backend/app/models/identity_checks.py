"""One attempt at an identity or bank check, and its outcome (B-387).

A check is a PAN verification, a penny-drop bank check with its name match, or a video in-person verification. An attempt is
made through a connected provider (only a simulated one exists, tagged `simulated:<key>`) or by the alternative method, a
person reading a document. Attempts are added and never edited. Only a PAN's masked form and a bank account's last four digits
are kept.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class VerificationAttempt(Base):
    __tablename__ = "verification_attempt"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="SET NULL"), nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8))                       # pan | bank | vipv
    method: Mapped[str] = mapped_column(String(12))                    # provider | alternative
    outcome: Mapped[str] = mapped_column(String(12))                   # passed | failed | review | unavailable
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the name match, 0 to 100
    name_shown: Mapped[str | None] = mapped_column(String(200), nullable=True)   # the name the source or document showed
    bank: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    pan_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    document: Mapped[str | None] = mapped_column(String(300), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
