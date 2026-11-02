"""What a person decided about a screening hit, and an EDD file's sign-off (L200-1 §5.2, §5.4).

Both are added and never edited. A screening result is the vendor's observation and is rewritten on every rescreen;
a decision on a hit is a person's and survives it, which is why the two are kept apart.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DECISION_KINDS = ("disposition", "confirmation", "qa")
DISPOSITIONS = ("true_match", "false_positive", "needs_info")
CONFIRMATIONS = ("confirmed", "rejected")
QA_RESULTS = ("agree", "overturn")
EDD_DECISIONS = ("approve", "decline", "more_info")


class ScreeningDecision(Base):
    """One decision on one hit for one party: a disposition, a second person's confirmation of a true match, or a
    QA reviewer agreeing with or overturning a false positive."""

    __tablename__ = "screening_decision"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_party.id", ondelete="CASCADE"), index=True)
    hit_key: Mapped[str] = mapped_column(String(240))      # category:matched name
    category: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_by_role: Mapped[str] = mapped_column(String(32))


class EddReview(Base):
    """A reviewer's decision on an EDD file: the wealth it explains, the source behind each item on that wealth's
    checklist, the conclusion, and approve, decline or ask for more."""

    __tablename__ = "edd_review"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True)
    wealth_type: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)   # checklist item -> the source a reviewer saw
    conclusion: Mapped[str] = mapped_column(String(16))          # explicable | not_explicable
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    risk_tier: Mapped[str | None] = mapped_column(String(16))
    reviewed_by: Mapped[str] = mapped_column(String(200))
    reviewed_by_role: Mapped[str] = mapped_column(String(32))
