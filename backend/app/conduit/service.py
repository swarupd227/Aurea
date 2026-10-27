"""Conduit service — provision connectors, run syncs, stamp lineage onto the client brain."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.pricing import PRICE_FRESH_DAYS, range_for_gap
from app.aurea_core.valuation import revalue_firm
from app.conduit.marketdata import fetch_feed
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


async def sync_market_data(
    session: AsyncSession, firm_id: uuid.UUID, *, revalue: bool = True, today: date | None = None,
    transport=None,
) -> dict:
    """Fetch REAL prices for public instruments, store them dated by the day they traded, and
    revalue the holdings they affect.

    Each instrument is fetched over the range that covers ITS gap since the last stored price,
    so a feed that has been off for months is caught up by the next run rather than leaving a
    hole. Settled history is never rewritten: a missing day is inserted, and an existing one is
    touched only if it is the latest stored day (it may have been a part-session price) or was
    not real. Prices are data, not decisions, so this runs on its own; the run is recorded
    with every symbol that failed and why.

    Returns a JSON-able report - it is stored on the ConnectorSync."""
    today = today or utcnow().date()
    instruments = (
        await session.execute(
            select(Instrument).where(
                Instrument.firm_id == firm_id,
                Instrument.market_type == MarketType.PUBLIC,
            )
        )
    ).scalars().all()
    report: dict = {"instruments": len(instruments), "resolved": 0, "inserted": 0, "updated": 0,
                    "failed": {}, "stale": [], "newest_price_date": None, "provider": None, "revalued": None}
    if not instruments:
        return report

    last_dates = {iid: d for iid, d in (await session.execute(
        select(Price.instrument_id, func.max(Price.as_of)).where(
            Price.firm_id == firm_id, Price.instrument_id.in_([i.id for i in instruments])
        ).group_by(Price.instrument_id)
    )).all()}
    ranges = {i.symbol: range_for_gap((today - last_dates[i.id]).days if i.id in last_dates else None)
              for i in instruments}

    feed = await fetch_feed([(i.symbol, i.market_symbol, i.currency) for i in instruments], ranges,
                            transport=transport)
    report["provider"] = feed.source
    report["failed"] = dict(feed.failures)
    by_symbol = {i.symbol: i for i in instruments}
    newest: dict[uuid.UUID, date] = {}

    for symbol, points in feed.series.items():
        inst = by_symbol.get(symbol)
        if inst is None:
            continue
        points = [(d, c) for d, c in points if d <= today]      # a price is never dated in the future
        if not points:
            report["failed"][symbol] = "only future-dated prices returned"
            continue
        report["resolved"] += 1
        last_stored = last_dates.get(inst.id)
        existing = {p.as_of: p for p in (await session.execute(
            select(Price).where(Price.instrument_id == inst.id, Price.as_of >= points[0][0])
        )).scalars().all()}
        currency = feed.currency.get(symbol) or inst.currency
        for d, close in points:
            row = existing.get(d)
            if row is None:
                session.add(Price(firm_id=firm_id, instrument_id=inst.id, as_of=d, close=close,
                                  currency=currency, source=feed.source, is_real=True))
                report["inserted"] += 1
            elif d == last_stored or not row.is_real:
                if abs(float(row.close) - close) > 1e-9 or not row.is_real:
                    row.close, row.source, row.is_real, row.currency = close, feed.source, True, currency
                    report["updated"] += 1
        newest[inst.id] = max(d for d, _ in points)

    for inst in instruments:
        latest = newest.get(inst.id) or last_dates.get(inst.id)
        if latest is None or (today - latest).days > PRICE_FRESH_DAYS:
            report["stale"].append({"symbol": inst.symbol, "latest": latest.isoformat() if latest else None})
    report["newest_price_date"] = max(newest.values()).isoformat() if newest else None

    await session.flush()
    if revalue and (report["inserted"] or report["updated"]):
        summary = await revalue_firm(session, firm_id)
        report["revalued"] = {"holdings": summary["holdings"], "real_priced": summary["real_priced"]}
    log.info("market_data_synced", firm_id=str(firm_id), resolved=report["resolved"],
             inserted=report["inserted"], updated=report["updated"], failed=len(report["failed"]),
             stale=len(report["stale"]))
    return report


async def run_sync(session: AsyncSession, connector: Connector) -> ConnectorSync:
    """Execute a connector sync. Market data hits a real feed; others record a mock sync."""
    started = utcnow()
    records = 0
    status = "success"
    detail: dict = {}
    try:
        if connector.domain == ConnectorDomain.MARKET_RESEARCH_DATA and not connector.use_mock:
            report = await sync_market_data(session, connector.firm_id)
            records = report["inserted"] + report["updated"]
            detail = {"mode": "live", "provider": connector.provider_key, **report}
            if report["instruments"] and report["resolved"] == 0:
                status = "error"
                connector.status = ConnectorStatus.ERROR
                connector.last_error = "No prices could be fetched: " + "; ".join(
                    f"{s}: {r}" for s, r in list(report["failed"].items())[:3])
            elif report["failed"]:
                status = "partial"
        else:
            # Mock connectors: data already present from seed; record a successful no-op sync.
            pdef = get_provider_def(connector.provider_key)
            detail = {"mode": "mock", "provider": connector.provider_key,
                      "note": "Serving realistic mock data; configure credentials to go live."}
            records = 0
        if status != "error":
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
    if status != "error":  # a run that fetched nothing is not a sync; the freshness view reads this
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
