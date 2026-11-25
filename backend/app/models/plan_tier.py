"""How much of a household's plan a trigger asks for (B-188): a full rebuild, or a review and refresh. A person sets it
with a reason; it is added and never edited, the latest stands, and nothing is worked out from the household's size.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

PLAN_TIERS = ("full_rebuild", "review_and_refresh")


class PlanTier(Base):
    __tablename__ = "plan_tier"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    tier: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    set_by: Mapped[str] = mapped_column(String(200))
