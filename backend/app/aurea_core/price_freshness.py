"""How fresh the prices under the book are - per instrument, on the basis each is carried at.

The question a person actually has is not "when did the connector last run" but "can I trust the
number next to this holding". A feed can run on schedule and still leave one symbol three months
old, and a private fund can sit on a placeholder forever. So this reads the prices themselves:
for every instrument somebody holds, the date and basis of the latest one, its age against the
limit that applies to it, and how much of the book rests on it. The feed's own record (last
run, what failed and why, when the next run is) sits beside that, not instead of it.

The limits are the ones billing already refuses to bill past (`billing.MAX_STALE_DAYS` for a
listed security, `pricing.PRIVATE_MAX_STALE_DAYS` for a private fund), so "stale" here and
"held for a stale valuation" there cannot disagree.

`SCHEDULE` is the one definition of when the worker fetches prices; the worker builds its
triggers from it and this reads the next run from it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.billing import MAX_STALE_DAYS
from app.aurea_core.pricing import PRICE_FRESH_DAYS, PRIVATE_MAX_STALE_DAYS, price_basis
from app.aurea_core.valuation import latest_prices
from app.core.db import utcnow
from app.models.connectors import Connector, ConnectorSync
from app.models.enums import ConnectorDomain, MarketType
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument

# (hour, minute, why) in UTC, Monday to Friday. 06:00 UTC is 19:00 NZDT / 18:00 NZST, after the
# NZX close; 22:00 UTC is 18:00 EDT / 17:00 EST, an hour or more after the US close.
SCHEDULE: list[tuple[int, int, str]] = [(6, 0, "after the NZX close"), (22, 0, "after the US close")]
SCHEDULE_DAYS = "mon-fri"

# A manual refresh asks the provider for every symbol again; asking twice in a row only
# invites the rate limit that makes the next scheduled run fail.
REFRESH_MIN_GAP_MINUTES = 10

# Worst first, so the view and a summary read in the order that needs attention.
STATUS_ORDER = ["missing", "synthetic", "stale", "ageing", "current"]


def next_runs(now: datetime | None = None, count: int = 2) -> list[dict]:
    """The next `count` scheduled price runs after `now`, each with why it is then."""
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out: list[dict] = []
    day = now.date()
    for _ in range(14):
        if day.weekday() < 5:
            for hour, minute, why in SCHEDULE:
                at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
                if at > now:
                    out.append({"at": at.isoformat(), "why": why})
        if len(out) >= count:
            break
        day += timedelta(days=1)
    return out[:count]


def classify(market_type: str, basis: str | None, age_days: int | None) -> tuple[str, int]:
    """(status, limit in days) for one instrument.

    missing    no price at all
    synthetic  a price nobody stated and no market gave - a seeded placeholder, usable for nothing
    stale      older than the limit that applies to it
    ageing     older than a long weekend, still inside the limit
    current    inside a long weekend (a private fund: inside its limit)"""
    private = market_type == MarketType.PRIVATE
    limit = PRIVATE_MAX_STALE_DAYS if private else MAX_STALE_DAYS
    if age_days is None:
        return "missing", limit
    if basis == "synthetic":
        return "synthetic", limit
    if age_days > limit:
        return "stale", limit
    if private:
        return "current", limit
    return ("current" if age_days <= PRICE_FRESH_DAYS else "ageing"), limit


async def price_freshness(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None,
                          now: datetime | None = None) -> dict:
    today = today or utcnow().date()
    held = {iid: (int(n), float(v or 0)) for iid, n, v in (await session.execute(
        select(Holding.instrument_id, func.count(Holding.id), func.sum(Holding.market_value))
        .where(Holding.firm_id == firm_id).group_by(Holding.instrument_id))).all()}
    instruments = (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id, Instrument.id.in_(list(held) or [uuid.uuid4()]))
    )).scalars().all()
    prices = await latest_prices(session, firm_id)

    rows = []
    for inst in instruments:
        p = prices.get(inst.id)
        basis = price_basis(p.source, p.is_real) if p else None
        age = (today - p.as_of).days if p else None
        status, limit = classify(inst.market_type, basis, age)
        n, value = held[inst.id]
        rows.append({
            "instrument_id": str(inst.id), "symbol": inst.symbol, "name": inst.name,
            "market_type": inst.market_type, "currency": inst.currency,
            "as_of": p.as_of.isoformat() if p else None, "age_days": age, "limit_days": limit,
            "basis": basis, "source": p.source if p else None, "status": status,
            "holdings": n, "value_held": round(value, 2),
        })
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), -(r["age_days"] or 0), -r["value_held"]))

    summary = {s: 0 for s in STATUS_ORDER}
    value_by = {s: 0.0 for s in STATUS_ORDER}
    for r in rows:
        summary[r["status"]] += 1
        value_by[r["status"]] += r["value_held"]
    total_value = sum(value_by.values())
    not_trusted = sum(value_by[s] for s in ("missing", "synthetic", "stale"))

    return {
        "as_of": today.isoformat(), "instruments": rows,
        "summary": {**summary, "total": len(rows),
                    "value_held": round(total_value, 2),
                    "value_not_current": round(not_trusted, 2),
                    "share_not_current": round(not_trusted / total_value, 4) if total_value else None},
        "feed": await feed_status(session, firm_id, now=now),
    }


async def statuses_by_mandate(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict[uuid.UUID, list[dict]]:
    """For each mandate, the price status of every instrument it holds - what a data-quality
    view needs to say which clients rest on a price that cannot be trusted."""
    today = today or utcnow().date()
    pairs = (await session.execute(
        select(Account.mandate_id, Holding.instrument_id).join(Account, Account.id == Holding.account_id)
        .where(Holding.firm_id == firm_id, Account.mandate_id.isnot(None)).distinct())).all()
    if not pairs:
        return {}
    instruments = {i.id: i for i in (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id,
                                 Instrument.id.in_({iid for _, iid in pairs})))).scalars().all()}
    prices = await latest_prices(session, firm_id)
    info: dict[uuid.UUID, dict] = {}
    for iid, inst in instruments.items():
        p = prices.get(iid)
        basis = price_basis(p.source, p.is_real) if p else None
        age = (today - p.as_of).days if p else None
        info[iid] = {"symbol": inst.symbol, "status": classify(inst.market_type, basis, age)[0], "age_days": age}
    out: dict[uuid.UUID, list[dict]] = {}
    for mandate_id, iid in pairs:
        if iid in info:
            out.setdefault(mandate_id, []).append(info[iid])
    return out


async def market_connector(session: AsyncSession, firm_id: uuid.UUID) -> Connector | None:
    return (await session.execute(
        select(Connector).where(Connector.firm_id == firm_id,
                                Connector.domain == ConnectorDomain.MARKET_RESEARCH_DATA)
        .order_by(Connector.use_mock.asc(), Connector.created_at.asc()))).scalars().first()


async def feed_status(session: AsyncSession, firm_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    """The price feed's own record: whether it is on, its last run and what failed, the next run."""
    conn = await market_connector(session, firm_id)
    if conn is None:
        return {"live": False, "connector": None, "last_run": None, "next_runs": [],
                "note": "No market-data connector is set up."}
    last = (await session.execute(
        select(ConnectorSync).where(ConnectorSync.connector_id == conn.id)
        .order_by(ConnectorSync.started_at.desc()).limit(1))).scalars().first()
    last_ok = conn.last_synced_at
    detail = (last.detail or {}) if last else {}
    live = not conn.use_mock
    return {
        "live": live, "connector": conn.display_name, "provider": conn.provider_key,
        "status": conn.status, "last_error": conn.last_error,
        "last_success_at": last_ok.isoformat() if last_ok else None,
        "last_run": None if last is None else {
            "status": last.status, "started_at": last.started_at.isoformat() if last.started_at else None,
            "inserted": detail.get("inserted", 0), "updated": detail.get("updated", 0),
            "resolved": detail.get("resolved"), "instruments": detail.get("instruments"),
            "failed": [{"symbol": s, "reason": r} for s, r in (detail.get("failed") or {}).items()],
        },
        "next_runs": next_runs(now) if live else [],
        "schedule": [{"hour": h, "minute": m, "why": w} for h, m, w in SCHEDULE],
        "note": None if live else "The live feed is switched off, so prices are not being fetched.",
    }


class RefreshRefused(Exception):
    """A refresh that should not run, with a reason fit to show the person."""


async def refresh_prices(session: AsyncSession, firm_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    """Fetch prices now, exactly as the scheduled run does, and say what changed.

    It goes through `run_sync`, so the run is recorded the same way and revalues the holdings
    it touched. Refused when the feed is off, or when it ran successfully moments ago."""
    from app.conduit.service import run_sync

    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    conn = await market_connector(session, firm_id)
    if conn is None or conn.use_mock:
        raise RefreshRefused("The live price feed is switched off, so there is nothing to refresh. "
                             "Turn it on under Connectors first.")
    if conn.last_synced_at is not None:
        last = conn.last_synced_at if conn.last_synced_at.tzinfo else conn.last_synced_at.replace(tzinfo=timezone.utc)
        wait = REFRESH_MIN_GAP_MINUTES - (now - last).total_seconds() / 60
        if wait > 0:
            raise RefreshRefused(f"Prices were fetched {int((now - last).total_seconds() // 60)} minute(s) ago. "
                                 f"Try again in {int(wait) + 1} minute(s) - asking the provider again straight "
                                 "away only gets the next run rate-limited.")
    sync = await run_sync(session, conn)
    d = sync.detail or {}
    return {
        "status": sync.status, "inserted": d.get("inserted", 0), "updated": d.get("updated", 0),
        "resolved": d.get("resolved", 0), "instruments": d.get("instruments", 0),
        "failed": [{"symbol": s, "reason": r} for s, r in (d.get("failed") or {}).items()],
        "newest_price_date": d.get("newest_price_date"), "revalued": d.get("revalued"),
        "error": d.get("error") or (conn.last_error if sync.status == "error" else None),
    }
