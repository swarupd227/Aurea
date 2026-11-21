"""Holdings a household has elsewhere, line by line (B-198).

Net worth carried money held elsewhere as a total, so nothing could say which part of it was doing badly. A
`HeldAwayHolding` is one line of a statement from another institution: the instrument as the statement names it,
how many, and the value it states on its date. It comes in two ways only: a person records it from the statement,
or an aggregation feed delivers it (`aurea_core/held_away_feeds.py`), and then `source_system` names the feed and
`external_id` is the feed's own id for the line. Nothing is estimated: the value is the statement's, and a line
with no price here is listed at that value with no performance.

Added and ended, never edited: a new statement ends the line it supersedes, which stays on file.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HeldAwayHolding(Base):
    __tablename__ = "held_away_holding"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    # Whose it is: a person, an entity (a trust), or neither for the household's jointly.
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    institution: Mapped[str] = mapped_column(String(120))
    account_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The instrument as the statement states it. Matched to a priced instrument by symbol when it is read.
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    instrument_name: Mapped[str] = mapped_column(String(200))
    asset_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(24, 6), nullable=True)
    value: Mapped[float] = mapped_column(Numeric(20, 2))   # as the statement states it
    currency: Mapped[str] = mapped_column(String(3))
    value_on: Mapped[date] = mapped_column(Date)           # the statement's date
    source_document: Mapped[str] = mapped_column(String(255))
    # The feed that delivered it and its id for the line; both empty on a line a person recorded.
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="current", index=True)  # current | ended
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
