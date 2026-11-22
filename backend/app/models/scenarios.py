"""A plan scenario a person saved on a household (B-179).

What was tried (the levers), what it did to the likelihoods and the funded ratio, and what it gives up, under the
assumption-set version it ran on. Saving one changes no record: no goal, income, mandate or plan is touched.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PlanScenario(Base):
    __tablename__ = "plan_scenario"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    levers: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    made_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    made_by_name: Mapped[str] = mapped_column(String(200))
