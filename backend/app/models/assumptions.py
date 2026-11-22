"""The firm's planning assumptions as one versioned record (B-174).

Each row is a whole version: every figure a projection runs on (returns and volatility by class, inflation by index,
retirement and plan-to ages, the success targets, the cash reserve, how old a planning input may be, the refresh line),
with the source of each figure the firm set. A version is added and never edited; the latest stands. Recording one
writes its figures through to `firm.settings`, which every engine already reads, and names the households whose
success moved by more than the refresh line.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AssumptionSetVersion(Base):
    __tablename__ = "assumption_set_version"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    figures: Mapped[dict] = mapped_column(JSON, default=dict)     # figure key -> value, the whole set as it stood
    sources: Mapped[dict] = mapped_column(JSON, default=dict)     # figure key -> where the firm's figure came from
    changed: Mapped[list] = mapped_column(JSON, default=list)     # the figures this version changed
    reason: Mapped[str] = mapped_column(String(400))
    made_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    made_by_name: Mapped[str] = mapped_column(String(200))
    refresh: Mapped[dict] = mapped_column(JSON, default=dict)     # households whose success moved, and the tasks raised
