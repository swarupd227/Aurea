"""Estate and NRI planning records for an Indian household (B-398), each recorded by a person from a document and added,
never edited: what a client wishes and the facts that bring a private trust into view, the yearly check of a will against
what is on file, a tax residency certificate or Form 10F, and a move out of or back to India with its checklist.

Nothing here changes an account, a nominee, a will, a residence record or a figure.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EstateWish(Base):
    """What a person said they wish, or a fact that brings a private trust into view, in their words."""

    __tablename__ = "estate_wish"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))            # wish | special_needs_beneficiary | business_succession | asset_protection
    asset_label: Mapped[str | None] = mapped_column(String(200), nullable=True)   # the asset it is about, if one
    words: Mapped[str] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class WillCheck(Base):
    """A person's check of a will against what is on file, with the assets as they stood that day."""

    __tablename__ = "will_check"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    checked_on: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(String(16))         # matches | needs_change
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"key", "label"}]: the assets on file when it was checked, each by what it is, its name and whose it is.
    snapshot: Mapped[list] = mapped_column(JSON, default=list)
    will_signed_on: Mapped[date] = mapped_column(Date)       # the will that was checked
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"), nullable=True)


class TreatyCertificate(Base):
    """A tax residency certificate from another country, or a Form 10F, as the document states it."""

    __tablename__ = "treaty_certificate"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))            # trc | form_10f
    country: Mapped[str] = mapped_column(String(80))
    reference: Mapped[str] = mapped_column(String(120))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class ResidenceMove(Base):
    """A client leaving India or returning, with the checklist as it stood when recorded and the tasks it raised."""

    __tablename__ = "residence_move"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10))       # leaving | returning
    moved_on: Mapped[date] = mapped_column(Date)             # the day of the move, past or planned
    country: Mapped[str] = mapped_column(String(80))         # where they go to or come from
    # [{"key", "label", "applies_to": [...], "figure": law figure key or null, "task_id": str or null}]
    items: Mapped[list] = mapped_column(JSON, default=list)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
