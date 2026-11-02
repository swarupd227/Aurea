"""A bulk repapering campaign and each household's answer (L200-1 §2). Rows are added; an answer is a new step."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RepaperingCampaign(Base):
    __tablename__ = "repapering_campaign"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    change_type: Mapped[str] = mapped_column(String(32))
    consent: Mapped[str] = mapped_column(String(12))            # negative | affirmative
    notice_days: Mapped[int | None] = mapped_column(Integer)
    document: Mapped[str] = mapped_column(String(300))          # the notice or agreement sent
    sent_on: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(200))


class RepaperingAnswer(Base):
    __tablename__ = "repapering_answer"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repapering_campaign.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)     # consented | objected | bounced, each with its evidence
