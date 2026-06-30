"""Conduit service — provision connectors, run syncs, stamp lineage onto the client brain."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conduit.marketdata import fetch_quotes
from app.conduit.registry import default_connectors, get_provider_def
from app.core.db import utcnow
from app.core.logging import get_logger
from app.models.connectors import Connector, ConnectorSync
from app.models.enums import ConnectorDomain, ConnectorStatus, MarketType
from app.models.portfolio import Instrument, Price

log = get_logger("aurea.conduit")


async def ensure_default_connectors(session: AsyncSession, firm_id: uuid.UUID) -> int:
    """Idempotently create one connector per domain for a firm."""
    existing = (
        await session.execute(select(Connector.provider_key).where(Connector.firm_id == firm_id))
    ).scalars().all()
    have = set(existing)
    created = 0
    for pdef in default_connectors():
        if pdef.key in have:
            continue
        live = pdef.supports_live and pdef.domain == ConnectorDomain.MARKET_RESEARCH_DATA
        session.add(
            Connector(
                firm_id=firm_id,
                domain=pdef.domain,
                provider_key=pdef.key,
                display_name=pdef.display_name,
                status=ConnectorStatus.CONNECTED if live else ConnectorStatus.MOCK,
                use_mock=not live,
                sync_cron=pdef.default_cron,
                config={},
            )
        )
        created += 1
    await session.flush()
    log.info("connectors_provisioned", firm_id=str(firm_id), created=created)
    return created


async def sync_market_data(session: AsyncSession, firm_id: uuid.UUID) -> int:
    """Fetch REAL quotes for public instruments and upsert today's Price rows (with lineage)."""
    instruments = (
        await session.execute(
            select(Instrument).where(
                Instrument.firm_id == firm_id,
                Instrument.market_type == MarketType.PUBLIC,
            )
        )
    ).scalars().all()
    if not instruments:
        return 0

    req = [(i.symbol, i.market_symbol, i.currency) for i in instruments]
    quotes = await fetch_quotes(req)
    by_symbol = {i.symbol: i for i in instruments}
    today = date.today()
    upserts = 0

    for symbol, quote in quotes.items():
        inst = by_symbol.get(symbol)
        if not inst:
            continue
        existing = (
            await session.execute(
                select(Price).where(Price.instrument_id == inst.id, Price.as_of == today)
            )
        ).scalar_one_or_none()
        if existing:
            existing.close = quote.close
            existing.source = quote.source
            existing.is_real = True
        else:
            session.add(
                Price(
                    firm_id=firm_id,
                    instrument_id=inst.id,
                    as_of=today,
                    close=quote.close,
                    currency=quote.currency,
                    source=quote.source,
                    is_real=True,
                )
            )
        upserts += 1

    await session.flush()
    log.info("market_data_synced", firm_id=str(firm_id), upserts=upserts)
    return upserts


async def run_sync(session: AsyncSession, connector: Connector) -> ConnectorSync:
    """Execute a connector sync. Market data hits a real feed; others record a mock sync."""
    started = utcnow()
    records = 0
    status = "success"
    detail: dict = {}
    try:
        if connector.domain == ConnectorDomain.MARKET_RESEARCH_DATA and not connector.use_mock:
            records = await sync_market_data(session, connector.firm_id)
            detail = {"mode": "live", "provider": connector.provider_key}
        else:
            # Mock connectors: data already present from seed; record a successful no-op sync.
            pdef = get_provider_def(connector.provider_key)
            detail = {"mode": "mock", "provider": connector.provider_key,
                      "note": "Serving realistic mock data; configure credentials to go live."}
            records = 0
        connector.status = (
            ConnectorStatus.CONNECTED if not connector.use_mock else ConnectorStatus.MOCK
        )
        connector.last_error = None
    except Exception as exc:  # pragma: no cover
        status = "error"
        connector.status = ConnectorStatus.ERROR
        connector.last_error = str(exc)
        detail = {"error": str(exc)}

    finished = utcnow()
    connector.last_synced_at = finished
    sync = ConnectorSync(
        firm_id=connector.firm_id,
        connector_id=connector.id,
        status=status,
        records_ingested=records,
        detail=detail,
        started_at=started,
        finished_at=finished,
    )
    session.add(sync)
    await session.flush()
    return sync
