"""What a person recorded for India's remaining items, batch E (B-322 to B-332): what an advisory agreement carried, the firm's mutual fund
cut-off figures, counsel's determination of the family of entities, and the advertisement approval a client-facing text cited. Each is added
and never edited: a correction is a new row and the latest for its key stands. Nothing here moves money, units or a holding, and nothing
here says a firm is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AgreementContent(Base):
    """Whether a household's signed advisory agreement carried Annexure A (SEBI's prescribed agreement) and the MITC (the most important terms
    and conditions), as a person read them off the signed document, or as an eSign envelope's template declared (B-324). `source` is `person` or
    `esign`; `item_id` names the eSign item. Nothing is assumed from a signature."""

    __tablename__ = "agreement_content"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    annexure_a: Mapped[bool] = mapped_column(Boolean, default=False)
    mitc: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="person")
    item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)


class MfFigure(Base):
    """One of the mutual fund cut-off figures (a cut-off time in minutes after midnight, or the large-amount line in rupees) as the firm recorded it
    from a circular (B-330). The latest row for a figure key stands; until one is recorded the figure is the platform's assumed default."""

    __tablename__ = "mf_figure"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    figure_key: Mapped[str] = mapped_column(String(40), index=True)
    value: Mapped[float] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(300))            # the circular, as the person names it
    as_of: Mapped[date] = mapped_column(Date)                   # the circular's date
    reason: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(String(200))


class FamilyDetermination(Base):
    """Counsel's determination of whether one group entity is within or outside the family of entities, with counsel's name, the day and the
    document (B-331). The latest row for an entity stands. Nothing is worked out from a name or a relationship."""

    __tablename__ = "family_determination"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    entity_name: Mapped[str] = mapped_column(String(200), index=True)
    determination: Mapped[str] = mapped_column(String(8))       # within | outside
    counsel_name: Mapped[str] = mapped_column(String(200))
    determined_on: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AdCitation(Base):
    """The advertisement approval a client-facing text cited when it went out (B-332): the item (a message or a report), the approval's id and the hash
    of the text that went. Kept so a page can show what stood behind what was sent."""

    __tablename__ = "ad_citation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    item_kind: Mapped[str] = mapped_column(String(10))          # message | report
    item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("household.id", ondelete="SET NULL"), nullable=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(index=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    cited_by: Mapped[str] = mapped_column(String(200))
