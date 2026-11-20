"""Where a record came from, when Astra's tables are a local copy of a Foundation system (B-161 to B-163).

Design: docs/03_Architecture/foundation-layer.md. A firm's domains (parties, accounts, positions, activity, prices,
crm) are each owned by a system: "native" (Astra's own tables, the default) or an adapter (Dynamics 365 for the CRM,
a wealth platform for the book). What an adapter supplies is upserted by (source, external_id) through
``external_ref``, so the Astra id every route, card and test id uses stays stable. A difference between the copy and
its source is a ``foundation_break``, reported with its evidence and never overwritten in either direction. What
Astra asks a Foundation system to record leaves as a ``foundation_command``, and the copy changes only when the
system's confirmation comes back through a sync.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Sourced:
    """Where a row came from. All three are empty on a row Astra made itself (a native firm's every row).

    ``source_system`` names the connector (e.g. "crm.dynamics"); it is not ``source``, because ``crm_contact.source``
    already says how a prospect came to the firm (referral, event) and ``price.source`` names the feed."""

    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalRef(Base):
    """A source's id for a record, mapped to Astra's. ``synced`` is what the last sync applied, field by field, so the
    next one can tell the source moving on (follow it) from the copy having been changed here (a break)."""

    __tablename__ = "external_ref"
    __table_args__ = (UniqueConstraint("firm_id", "source", "entity_type", "external_id", name="uq_external_ref"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    local_id: Mapped[uuid.UUID] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(128))
    synced: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FoundationBreak(Base):
    """The copy and its source disagree on a record, with the evidence: each field's value here, at the source, and as
    last synced. Neither side is changed. A later sync on which they agree clears it."""

    __tablename__ = "foundation_break"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(16), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    local_id: Mapped[uuid.UUID] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(128))
    fields: Mapped[list] = mapped_column(JSON, default=list)   # [{field, here, source, synced}]
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)   # open | cleared
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FoundationCommand(Base):
    """Something Astra asked a Foundation system to record (a CRM activity today). Pending until the system's own
    record of it comes back through a sync, then confirmed with that record's id; failed if the system refused it."""

    __tablename__ = "foundation_command"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connector.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)   # pending | confirmed | failed
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)    # the row the confirmation became
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
