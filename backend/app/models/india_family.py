"""What a person recorded about an Indian client's family and estate (India row 13), each from a document.

Added and never edited: a change is a new row, and the latest for its subject stands. Nothing here is worked out, and nothing
changes a holding, a nominee on an account or a figure in a plan.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SebiRelation(Base):
    """One person's place in another's SEBI "family of client": the spouse, a child or a parent, and whether the child or parent is
    dependent on them. A person with no row is not placed in any family beyond their household (B-245)."""

    __tablename__ = "sebi_relation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)        # the one being placed
    of_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)     # whose family it is
    relation: Mapped[str] = mapped_column(String(12))        # spouse | child | parent | none (not dependent, a family of their own)
    dependent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AssetNomination(Base):
    """What a client recorded for one asset's nomination: nominees, or a signed opt-out (B-232)."""

    __tablename__ = "asset_nomination"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    asset_kind: Mapped[str] = mapped_column(String(16))      # demat | mutual_fund | bank_account | epf
    label: Mapped[str] = mapped_column(String(200))          # the account or folio, as the document names it
    status: Mapped[str] = mapped_column(String(12))          # nominated | opted_out
    nominees: Mapped[list | None] = mapped_column(JSON, nullable=True)       # [{"name", "relationship", "share"}]
    declaration_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # the day the opt-out declaration was signed
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class WillStatus(Base):
    """Where a person's will stands, and the personal law their succession follows, as a document says (B-232)."""

    __tablename__ = "will_status"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    personal_law: Mapped[str | None] = mapped_column(String(16), nullable=True)    # hindu | muslim | christian | parsi | other
    registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    probate: Mapped[str | None] = mapped_column(String(16), nullable=True)         # not_sought | applied | granted | not_required
    probate_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    executor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # B-398: who attested the will, and whether the executor has been told, as a person recorded them. Null: not recorded.
    witnesses: Mapped[list | None] = mapped_column(JSON, nullable=True)
    executor_told: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    executor_told_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    as_of: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class HufRecord(Base):
    """Who is karta and who the coparceners are, as the HUF's deed or a declaration names them. The latest stands."""

    __tablename__ = "huf_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), index=True)
    karta_name: Mapped[str] = mapped_column(String(200))
    karta_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    coparceners: Mapped[list | None] = mapped_column(JSON, nullable=True)          # [{"name", "relationship", "person_id"}]
    as_of: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class GratuityRecord(Base):
    """The gratuity a person's employer statement says is payable on leaving, as a lump sum (B-233). Never estimated. A later statement for the same employer replaces it; the earlier one stays on file."""

    __tablename__ = "gratuity_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    employer: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Numeric(20, 2))
    years_of_service: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    payable_on: Mapped[date | None] = mapped_column(Date, nullable=True)           # the day the statement expects it, if it says
    as_of: Mapped[date] = mapped_column(Date)                                      # the statement's date
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
