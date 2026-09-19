"""A CRM pipeline as its own object (L200-8 §2.1).

L200-8 names CRM as a first-class "client master" component of every reference
architecture, distinct from the portfolio/billing platform — contacts, pipeline stages, an
activity log. `Household`/`Person` (app.models.graph) are the client graph for someone
already onboarding or onboarded; there is deliberately no way to represent a prospect who
has not yet become either — `Meeting` (app.models.engagement) requires a household_id, for
instance. These three models fill exactly that gap, staying structurally separate from the
client graph the way L200-8 keeps them, and converge into it only on conversion.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

CONTACT_TYPES = ("prospect", "referral_source", "centre_of_influence", "client_contact")
PIPELINE_STAGES = ("lead", "qualified", "proposal", "won", "lost")
OPEN_STAGES = ("lead", "qualified", "proposal")
ACTIVITY_TYPES = ("call", "email", "meeting", "note", "task")


class CrmContact(Base):
    """A person the firm has a relationship with who is not (yet, or ever) a client."""

    __tablename__ = "crm_contact"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(48), nullable=True)
    contact_type: Mapped[str] = mapped_column(String(24), default="prospect", index=True)
    # Set once (if ever) this contact converts to an onboarded client — the one deliberate
    # seam back into the client graph.
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)   # referral, event, inbound, cold...
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class CrmOpportunity(Base):
    """One pipeline deal against a contact — lead through won/lost."""

    __tablename__ = "crm_opportunity"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_contact.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(16), default="lead", index=True)
    estimated_aum: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    probability_pct: Mapped[int] = mapped_column(Integer, default=10)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set on "won" once the resulting household exists — the pipeline's own conversion record.
    converted_household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True
    )


class CrmActivity(Base):
    """The activity log: every call, email, meeting or note against a contact/opportunity."""

    __tablename__ = "crm_activity"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_contact.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm_opportunity.id", ondelete="SET NULL"), nullable=True, index=True
    )
    activity_type: Mapped[str] = mapped_column(String(16), default="note")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str] = mapped_column(Text)
    logged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
