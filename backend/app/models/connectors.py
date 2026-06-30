"""Conduit connector configuration + sync history (spec §11, Table 17)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import ConnectorDomain, ConnectorStatus


class Connector(Base):
    """A configured integration. Ships with mock data; ready to flip live via `config`.

    `provider_key` references a registered provider definition in app.conduit.registry
    (e.g. 'custody.generic', 'marketdata.stooq', 'crm.salesforce')."""

    __tablename__ = "connector"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    domain: Mapped[ConnectorDomain] = mapped_column(String(32), index=True)
    provider_key: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[ConnectorStatus] = mapped_column(String(16), default=ConnectorStatus.MOCK)
    # Whether this connector serves mock data or attempts a live connection.
    use_mock: Mapped[bool] = mapped_column(Boolean, default=True)
    # Connection config: credentials (write-only in API), endpoints, sync cadence, mappings.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    sync_cron: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    syncs: Mapped[list["ConnectorSync"]] = relationship(
        back_populates="connector", cascade="all, delete-orphan"
    )


class ConnectorSync(Base):
    """An execution of a connector sync — feeds the lineage trail."""

    __tablename__ = "connector_sync"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="success")
    records_ingested: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connector: Mapped["Connector"] = relationship(back_populates="syncs")
