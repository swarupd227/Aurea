"""Advisor-defined skills — plain-English agents the firm authors itself. A skill is data; at run
time it executes as a governed Atlas agent (sense → think with Claude → check → decide → ledger), so
anything an adviser builds inherits the same governance as the built-in workforce."""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Skill(Base):
    __tablename__ = "skill"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # owner
    # private (owner only) · shared (owner + shared_with) · public (everyone in the firm)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    shared_with: Mapped[list] = mapped_column(JSON, default=list)  # user ids the skill is shared with
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    # The plain-English instruction Claude runs over each client's brain.
    instruction: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(16), default="book")     # household | book
    output_kind: Mapped[str] = mapped_column(String(16), default="insight")  # insight | task | note
    # Skills are assistive only — capped at Tier 2, never autonomous execution.
    default_tier: Mapped[str] = mapped_column(String(16), default="tier_1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
