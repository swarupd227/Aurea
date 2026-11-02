"""What a firm keeps on file about a client beyond their money (L100 §5.3, §5.4, §5.8). A person records each from a
document or a meeting note. Added and never edited: a change is a new row.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DOCUMENT_KINDS = ("will", "revocable_trust", "power_of_attorney", "health_directive", "trust_deed")
LIFE_EVENTS = ("marriage", "divorce", "birth", "death_in_family", "inheritance", "business_sale", "relocation", "job_change", "retirement", "other")


class TrustedContact(Base):
    """Who a firm may call if it is worried about a client, who has no authority to act for them."""

    __tablename__ = "trusted_contact"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    relationship: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(48))
    email: Mapped[str | None] = mapped_column(String(254))
    as_of: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class EstateDocument(Base):
    __tablename__ = "estate_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    signed_on: Mapped[date] = mapped_column(Date)
    held_by: Mapped[str | None] = mapped_column(String(200))
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class LifeEvent(Base):
    __tablename__ = "life_event"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    happened_on: Mapped[date] = mapped_column(Date)
    other_party: Mapped[str | None] = mapped_column(String(200))    # the former spouse, the person who died
    note: Mapped[str | None] = mapped_column(Text)
    source_document: Mapped[str] = mapped_column(String(300))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
