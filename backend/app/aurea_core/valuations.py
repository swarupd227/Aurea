"""Declared valuations for private holdings (L100 §9).

A private fund has no feed, so until this existed its price was an invented 100.0 that nothing
could update - which kept every mandate holding it stale for billing, flat in performance and
"synthetic" in data quality forever. This lets a person record what the manager actually
stated, with the document it came from.

The rules, each one a refusal rather than a convention:

  * No evidence, no value. A source document is required.
  * Private instruments only. A listed security's price comes from the market, never a person.
  * No rewriting history. A valuation cannot be dated before the latest one already declared;
    a second one for the same date supersedes the first and says so.
  * A big move is explained. More than 50% against the previous value needs a note, because
    a typo (10x, a dropped digit) is the likeliest cause of a move that large - and this number
    drives client balances and the fee charged on them.
  * Never a future date, never zero or negative.

It writes a Price row marked `source="declared_valuation"` and NOT real: that is what keeps
the execution engine refusing to fill on it (nothing may trade at a stated NAV), while
`pricing.price_basis` lets every consumer call it "declared" rather than "synthetic".
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.pricing import DECLARED_SOURCE, PRIVATE_MAX_STALE_DAYS, price_basis
from app.aurea_core.valuation import latest_prices, revalue_firm
from app.core.db import utcnow
from app.models.enums import MarketType
from app.models.portfolio import Holding, Instrument, Price
from app.models.valuations import ValuationStatement

LARGE_MOVE = 0.5


class ValuationError(Exception):
    """A valuation that cannot be recorded, with a reason fit to show the user."""


async def record_valuation(
    session: AsyncSession, firm_id: uuid.UUID, *, instrument_id: uuid.UUID, as_of: date, unit_value: float,
    source_document: str, recorded_by: str, notes: str | None = None, today: date | None = None,
) -> dict:
    today = today or utcnow().date()
    inst = await session.get(Instrument, instrument_id)
    if inst is None or inst.firm_id != firm_id:
        raise ValuationError("Instrument not found.")
    if inst.market_type != MarketType.PRIVATE:
        raise ValuationError(f"{inst.symbol} is a listed security; its price comes from the market, not a statement.")
    if not source_document or len(source_document.strip()) < 5:
        raise ValuationError("A source document is required - say which statement this value comes from.")
    if unit_value is None or float(unit_value) <= 0:
        raise ValuationError("The unit value must be greater than zero.")
    if as_of > today:
        raise ValuationError("A valuation cannot be dated in the future.")

    unit_value = float(unit_value)
    active = (await session.execute(
        select(ValuationStatement).where(ValuationStatement.instrument_id == inst.id,
                                         ValuationStatement.status == "active")
        .order_by(ValuationStatement.as_of.desc())
    )).scalars().all()
    if active and as_of < active[0].as_of:
        raise ValuationError(
            f"A valuation dated {active[0].as_of.isoformat()} is already on record; "
            "an earlier one cannot be added behind it.")

    prices = await latest_prices(session, firm_id)
    prior = prices.get(inst.id)
    prior_value = float(prior.close) if prior else None
    # A same-date re-declaration replaces the old one, so compare against what came BEFORE it.
    if prior is not None and prior.as_of == as_of:
        earlier = (await session.execute(
            select(Price).where(Price.instrument_id == inst.id, Price.as_of < as_of)
            .order_by(Price.as_of.desc()).limit(1))).scalars().first()
        prior_value, prior_date, prior_basis = (
            (float(earlier.close), earlier.as_of, price_basis(earlier.source, earlier.is_real))
            if earlier else (None, None, None))
    else:
        prior_date = prior.as_of if prior else None
        prior_basis = price_basis(prior.source, prior.is_real) if prior else None

    change = (unit_value / prior_value - 1.0) if prior_value else None
    if change is not None and abs(change) > LARGE_MOVE and not (notes and notes.strip()):
        raise ValuationError(
            f"That is a {change:+.0%} move against {prior_value:,.4f}. A move this large needs a note "
            "explaining it - it is more often a typo than a revaluation.")

    held = (await session.execute(
        select(Holding).where(Holding.firm_id == firm_id, Holding.instrument_id == inst.id))).scalars().all()
    value_before = sum(float(h.market_value or 0) for h in held)

    for old in active:
        if old.as_of == as_of:
            old.status = "superseded"
    row = (await session.execute(
        select(Price).where(Price.instrument_id == inst.id, Price.as_of == as_of))).scalar_one_or_none()
    if row is None:
        session.add(Price(firm_id=firm_id, instrument_id=inst.id, as_of=as_of, close=unit_value,
                          currency=inst.currency, source=DECLARED_SOURCE, is_real=False))
    else:
        row.close, row.source, row.is_real, row.currency = unit_value, DECLARED_SOURCE, False, inst.currency
    session.add(ValuationStatement(
        firm_id=firm_id, instrument_id=inst.id, as_of=as_of, unit_value=unit_value, currency=inst.currency,
        source_document=source_document.strip(), recorded_by=recorded_by, notes=(notes or "").strip() or None,
        previous_value=prior_value, previous_as_of=prior_date, status="active"))
    await session.flush()

    await revalue_firm(session, firm_id)
    for h in held:
        await session.refresh(h)
    value_after = sum(float(h.market_value or 0) for h in held)
    return {
        "instrument_id": str(inst.id), "symbol": inst.symbol, "name": inst.name, "currency": inst.currency,
        "as_of": as_of.isoformat(), "unit_value": unit_value, "source_document": source_document.strip(),
        "previous": {"value": prior_value, "as_of": prior_date.isoformat() if prior_date else None,
                     "basis": prior_basis} if prior_value is not None else None,
        "change_pct": round(change, 4) if change is not None else None,
        "holdings_revalued": len(held), "value_before": round(value_before, 2), "value_after": round(value_after, 2),
        "recorded_by": recorded_by,
    }


async def private_valuations(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> list[dict]:
    """Every private instrument: what it is carried at, on what basis, how old that is, and how
    much of the book depends on it."""
    today = today or utcnow().date()
    instruments = (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id, Instrument.market_type == MarketType.PRIVATE)
        .order_by(Instrument.symbol))).scalars().all()
    prices = await latest_prices(session, firm_id)
    out = []
    for inst in instruments:
        p = prices.get(inst.id)
        agg = (await session.execute(
            select(func.count(Holding.id), func.coalesce(func.sum(Holding.market_value), 0))
            .where(Holding.firm_id == firm_id, Holding.instrument_id == inst.id))).one()
        latest = (await session.execute(
            select(ValuationStatement).where(ValuationStatement.instrument_id == inst.id,
                                             ValuationStatement.status == "active")
            .order_by(ValuationStatement.as_of.desc()).limit(1))).scalars().first()
        age = (today - p.as_of).days if p else None
        out.append({
            "instrument_id": str(inst.id), "symbol": inst.symbol, "name": inst.name, "currency": inst.currency,
            "unit_value": float(p.close) if p else None, "as_of": p.as_of.isoformat() if p else None,
            "basis": price_basis(p.source, p.is_real) if p else None, "age_days": age,
            "limit_days": PRIVATE_MAX_STALE_DAYS,
            "overdue": age is None or age > PRIVATE_MAX_STALE_DAYS or price_basis(p.source, p.is_real) != "declared",
            "holdings": int(agg[0]), "value_held": round(float(agg[1]), 2),
            "source_document": latest.source_document if latest else None,
            "recorded_by": latest.recorded_by if latest else None,
        })
    return out
