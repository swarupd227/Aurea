"""Real-time valuation & aggregation (spec §6.2).

Resolves each holding's market value from the latest price and rolls up to account,
mandate, entity and household totals — the total-portfolio view across public and private
markets. Every figure carries lineage (price source + as_of) and an aggregate confidence."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument, Price

log = get_logger("aurea.valuation")


async def latest_prices(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, Price]:
    """Most recent price per instrument for a firm."""
    rows = (
        await session.execute(
            select(Price).where(Price.firm_id == firm_id).order_by(Price.as_of.desc())
        )
    ).scalars().all()
    out: dict[uuid.UUID, Price] = {}
    for p in rows:
        out.setdefault(p.instrument_id, p)  # first seen = latest due to ordering
    return out


async def revalue_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Recompute market_value for every holding from latest prices. Returns a summary."""
    prices = await latest_prices(session, firm_id)
    holdings = (
        await session.execute(select(Holding).where(Holding.firm_id == firm_id))
    ).scalars().all()
    instruments = {
        i.id: i
        for i in (
            await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))
        ).scalars().all()
    }

    total = 0.0
    real_priced = 0
    for h in holdings:
        price = prices.get(h.instrument_id)
        inst = instruments.get(h.instrument_id)
        if price is None:
            continue
        mv = float(h.quantity) * float(price.close)
        h.market_value = mv
        h.lineage = {
            "price_source": price.source,
            "price_as_of": price.as_of.isoformat(),
            "is_real_price": price.is_real,
            "instrument": inst.symbol if inst else None,
        }
        # Confidence: real public price = high; synthetic/private = lower.
        h.confidence = 0.99 if price.is_real else (0.7 if inst and inst.market_type == "private" else 0.85)
        total += mv
        if price.is_real:
            real_priced += 1

    await session.flush()
    log.info("revalued_firm", firm_id=str(firm_id), holdings=len(holdings), total=round(total, 2))
    return {"holdings": len(holdings), "total_value": total, "real_priced": real_priced}


async def account_valuation(session: AsyncSession, account: Account) -> dict:
    """Per-instrument and class-level valuation for a single account."""
    prices = await latest_prices(session, account.firm_id)
    holdings = (
        await session.execute(
            select(Holding).where(Holding.account_id == account.id)
        )
    ).scalars().all()
    inst_ids = [h.instrument_id for h in holdings]
    instruments = {
        i.id: i
        for i in (
            await session.execute(select(Instrument).where(Instrument.id.in_(inst_ids)))
        ).scalars().all()
    } if inst_ids else {}

    by_class: dict[str, float] = defaultdict(float)
    positions = []
    total = float(account.cash_balance or 0)
    by_class["cash"] += float(account.cash_balance or 0)
    confidences = []
    for h in holdings:
        inst = instruments.get(h.instrument_id)
        price = prices.get(h.instrument_id)
        mv = float(h.market_value or 0)
        total += mv
        if inst:
            by_class[inst.asset_class] += mv
        confidences.append(float(h.confidence or 1.0))
        positions.append(
            {
                "instrument": inst.symbol if inst else "?",
                "name": inst.name if inst else "",
                "asset_class": inst.asset_class if inst else "unknown",
                "market_type": inst.market_type if inst else "public",
                "quantity": float(h.quantity),
                "market_value": round(mv, 2),
                "cost_basis": round(float(h.cost_basis or 0), 2),
                "unrealised_gain": round(mv - float(h.cost_basis or 0), 2),
                "price_source": price.source if price else None,
                "price_as_of": price.as_of.isoformat() if price else None,
                "confidence": round(float(h.confidence or 1.0), 3),
            }
        )
    return {
        "total_value": round(total, 2),
        "by_asset_class": {k: round(v, 2) for k, v in by_class.items()},
        "positions": positions,
        "data_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 1.0,
        "as_of": date.today().isoformat(),
    }
