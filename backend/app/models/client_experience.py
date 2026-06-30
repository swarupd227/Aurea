"""Aurea Canvas client-experience models (spec §9, Table 14): secure messaging and the
next-gen / heir onboarding journey."""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import HeirJourneyStatus, MessageAuthor


class Message(Base):
    """A secure message in a client–adviser thread (one thread per household).

    Keeps the relationship continuous between meetings; adviser-approved agent outreach is
    delivered here, so the client experiences continuity, not a channel switch."""

    __tablename__ = "message"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), index=True
    )
    author_role: Mapped[MessageAuthor] = mapped_column(String(12))
    author_name: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    # Provenance: if this was an approved agent outreach, the recommendation it came from.
    source_recommendation_id: Mapped[uuid.UUID | None] = mapped_column()
    read_by_client: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by_adviser: Mapped[bool] = mapped_column(Boolean, default=False)


class HeirJourney(Base):
    """A digital-first, education-led onboarding journey for a next-gen heir (spec §9, Table 15).

    Retention through the wealth transfer: engage heirs early, on their own terms."""

    __tablename__ = "heir_journey"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), index=True
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[HeirJourneyStatus] = mapped_column(String(16), default=HeirJourneyStatus.INVITED)
    # Step completion + any captured values/preferences.
    steps: Mapped[list] = mapped_column(JSON, default=list)
    captured: Mapped[dict] = mapped_column(JSON, default=dict)


def default_heir_steps() -> list[dict]:
    """The education-led, digital-first journey for a next-gen heir."""
    return [
        {"key": "welcome", "title": "Welcome", "done": False,
         "blurb": "Your family has built something meaningful. This is your space to understand it — "
                  "on your own terms, at your own pace."},
        {"key": "learn", "title": "How wealth works", "done": False,
         "blurb": "Short, jargon-free lessons: diversification, compounding, risk, and why a long "
                  "horizon is your biggest advantage."},
        {"key": "values", "title": "What matters to you", "done": False,
         "blurb": "Tell us the causes and themes you care about — your portfolio can reflect them."},
        {"key": "explore", "title": "Your family's plan", "done": False,
         "blurb": "See the goals your family is working toward and how the plan stays on track."},
        {"key": "connect", "title": "Meet your adviser", "done": False,
         "blurb": "Say hello to your named adviser — they're here whenever you have a question."},
    ]
