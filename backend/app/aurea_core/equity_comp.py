"""Equity compensation and concentrated stock (L100 §5.2, §5.4).

A grant is recorded by a person from the plan statement, and what has vested comes from its schedule and today's
date. Value comes only from a real price: a grant with no price or no symbol is listed with no value. An option
is worth what it is in the money. Grants sit beside net worth and are not added to it, because what has vested may
already be recorded there or held in an account.

An equity position is concentrated at the same line the book's signals use, more than a fifth of what is measured:
a bond holding is not single-stock risk, and a broad equity fund cannot be told apart, because an instrument does
not say what it holds. The scenarios lay out selling it over a few years, from the tax lots, so an adviser can put a three-year and a
seven-year path side by side. They hold the price flat and stop at the gain: the tax on it, AMT on an ISO and any
withholding gap are for the client's CPA, and are named as not modelled. A plan is the adviser's and the client's,
recorded from what they agreed, and the actual position is read against it. Nothing here places an order.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, shelf
from app.aurea_core.billing import _mandate_households
from app.aurea_core.valuation import latest_prices
from app.models.equity_comp import GRANT_KINDS, OPTION_KINDS, PLAN_METHODS, ConcentrationPlan, EquityGrant
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, TaxLot
from app.models.tenant import Firm
from app.provenance import ledger

CONCENTRATED = 0.20
SCENARIO_YEARS = (3, 5, 7)
VESTING_WINDOW_DAYS = 365
NOT_MODELLED = ["tax_on_the_gain", "amt_on_isos", "withholding_gap", "price_moves", "blackout_windows", "insider_rules"]


class EquityCompError(ValueError):
    """This cannot be recorded, with the reason."""


def _vested(grant: EquityGrant, today: date) -> float:
    if not grant.vesting:
        return float(grant.units)
    return min(float(grant.units), sum(float(v["units"]) for v in grant.vesting if date.fromisoformat(v["on"]) <= today))


def _grant_view(grant: EquityGrant, price: float | None, today: date, person: str | None) -> dict:
    units, vested = float(grant.units), _vested(grant, today)
    worth = None
    if price is not None:
        worth = max(price - float(grant.strike or 0), 0.0) if grant.kind in OPTION_KINDS else price
    window = today + timedelta(days=VESTING_WINDOW_DAYS)
    coming = [v for v in grant.vesting or [] if today < date.fromisoformat(v["on"]) <= window]
    upcoming = sum(float(v["units"]) for v in coming)
    nearest = min((v["on"] for v in coming), default=None)
    return {
        "id": str(grant.id), "person_id": str(grant.person_id), "person": person, "kind": grant.kind, "employer": grant.employer, "symbol": grant.symbol,
        "units": units, "strike": float(grant.strike) if grant.strike is not None else None, "grant_date": grant.grant_date.isoformat(),
        "vested": vested, "unvested": units - vested, "expires_on": grant.expires_on.isoformat() if grant.expires_on else None,
        "expired": bool(grant.expires_on and grant.expires_on < today), "price": price, "value_vested": round(vested * worth, 2) if worth is not None else None,
        "value_unvested": round((units - vested) * worth, 2) if worth is not None else None, "next_vest_on": nearest, "vesting_12m": upcoming,
        "value_vesting_12m": round(upcoming * worth, 2) if worth is not None else None, "source_document": grant.source_document,
    }


async def _positions(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, dict]:
    """Each household's book: its total, and what it holds of each instrument with the lots' cost."""
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True)))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    accounts = (await session.execute(select(Account).where(Account.firm_id == firm_id, Account.status == "active"))).scalars().all()
    home = {a.id: owners.get(a.mandate_id) for a in accounts if a.mandate_id in owners and owners.get(a.mandate_id)}
    book: dict[uuid.UUID, dict] = defaultdict(lambda: {"total": 0.0, "holdings": {}})
    funds = await shelf.diversified(session, firm_id)
    for a in accounts:
        if a.id in home:
            book[home[a.id]]["total"] += float(a.cash_balance or 0)
    rows = (await session.execute(select(Holding, Instrument).join(Instrument, Instrument.id == Holding.instrument_id)
                                  .where(Holding.firm_id == firm_id, Holding.quantity > 0))).all()
    held = [h.id for h, _ in rows]
    lots = (await session.execute(select(TaxLot).where(TaxLot.holding_id.in_(held)))).scalars().all() if held else []
    cost: dict[uuid.UUID, float] = defaultdict(float)
    for lot in lots:
        cost[lot.holding_id] += float(lot.quantity) * float(lot.cost_per_unit)
    for h, inst in rows:
        household = home.get(h.account_id)
        if household is None:
            continue
        book[household]["total"] += float(h.market_value or 0)
        row = book[household]["holdings"].setdefault(inst.symbol, {"symbol": inst.symbol, "name": inst.name, "asset_class": "fund" if inst.id in funds else str(inst.asset_class),
                                                                  "quantity": 0.0, "value": 0.0, "cost": 0.0})
        row["quantity"] += float(h.quantity)
        row["value"] += float(h.market_value or 0)
        row["cost"] += cost[h.id]
    return book


def _scenarios(position: dict, total: float, target: float) -> list[dict]:
    """Selling down to `target` evenly over a few years, at today's price, with the gain at average cost."""
    keep = target * total
    to_sell = max(position["value"] - keep, 0.0)
    gain_share = 1 - position["cost"] / position["value"] if position["value"] and position["cost"] else None
    return [{"years": n, "sold_a_year": round(to_sell / n, 2), "units_a_year": round(to_sell / n / (position["value"] / position["quantity"]), 2),
             "gain_a_year": round(to_sell / n * gain_share, 2) if gain_share is not None else None} for n in SCENARIO_YEARS]


async def _plans(session: AsyncSession, firm_id: uuid.UUID) -> dict[tuple, ConcentrationPlan]:
    rows = (await session.execute(select(ConcentrationPlan).where(ConcentrationPlan.firm_id == firm_id).order_by(ConcentrationPlan.created_at))).scalars().all()
    return {(r.household_id, r.symbol): r for r in rows}


def _exposures(household_id: uuid.UUID, position_book: dict, grants: list[dict], plans: dict, today: date) -> list[dict]:
    total = position_book["total"]
    granted: dict[str, float] = defaultdict(float)
    for g in grants:
        if g["symbol"] and g["value_vested"] is not None:
            granted[g["symbol"]] += g["value_vested"] + (g["value_unvested"] or 0)
    out = []
    for symbol in sorted(set(position_book["holdings"]) | set(granted)):
        position = position_book["holdings"].get(symbol)
        held = position["value"] if position else 0.0
        if position and position["asset_class"] != "equity":
            continue
        measured = total + granted[symbol]
        weight = (held + granted[symbol]) / measured if measured else 0.0
        plan = plans.get((household_id, symbol))
        if weight <= CONCENTRATED and plan is None:
            continue
        target = float(plan.target_weight) if plan else CONCENTRATED
        out.append({
            "symbol": symbol, "name": position["name"] if position else symbol, "held": round(held, 2), "granted": round(granted[symbol], 2), "weight": round(weight, 4),
            "concentrated": weight > CONCENTRATED, "cost": round(position["cost"], 2) if position else None,
            "plan": {"id": str(plan.id), "method": plan.method, "target_weight": target, "target_date": plan.target_date.isoformat(),
                     "rationale": plan.rationale, "document": plan.source_document, "reached": weight <= target,
                     "overdue": plan.target_date < today and weight > target} if plan else None,
            "scenarios": _scenarios(position, total, target) if position and position["quantity"] else [],
        })
    return sorted(out, key=lambda e: -e["weight"])


def _household_view(household: Household, grants: list[dict], exposures: list[dict]) -> dict:
    return {"household_id": str(household.id), "household": household.name, "grants": grants, "exposures": exposures,
            "value_vested": round(sum(g["value_vested"] or 0 for g in grants), 2), "value_unvested": round(sum(g["value_unvested"] or 0 for g in grants), 2),
            "value_vesting_12m": round(sum(g["value_vesting_12m"] or 0 for g in grants), 2),
            "next_vest_on": min((g["next_vest_on"] for g in grants if g["next_vest_on"]), default=None),
            "unpriced": [g["id"] for g in grants if g["price"] is None], "not_modelled": NOT_MODELLED}


async def _load(session: AsyncSession, firm_id: uuid.UUID) -> tuple:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    prices = await latest_prices(session, firm_id)
    by_symbol = {i.symbol: i.id for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()}
    people = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm_id))).scalars().all()}
    rows = (await session.execute(select(EquityGrant).where(EquityGrant.firm_id == firm_id, EquityGrant.ended_on.is_(None))
                                  .order_by(EquityGrant.grant_date))).scalars().all()
    grants: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for g in rows:
        price = prices.get(by_symbol.get(g.symbol)) if g.symbol else None
        grants[g.household_id].append(_grant_view(g, float(price.close) if price is not None and price.is_real else None, today, people.get(g.person_id)))
    return today, grants, await _positions(session, firm_id), await _plans(session, firm_id)


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    today, grants, book, plans = await _load(session, firm_id)
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    view = _household_view(household, grants[household_id], _exposures(household_id, book.get(household_id, {"total": 0.0, "holdings": {}}), grants[household_id], plans, today))
    return {**view, "people": [{"person_id": str(p.id), "name": p.full_name} for p in people], "as_of": today.isoformat()}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household with a grant or a concentrated position."""
    today, grants, book, plans = await _load(session, firm_id)
    households = {h.id: h for h in (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None)))).scalars().all()}
    rows = []
    for household_id, household in households.items():
        exposures = _exposures(household_id, book.get(household_id, {"total": 0.0, "holdings": {}}), grants[household_id], plans, today)
        if grants[household_id] or exposures:
            rows.append(_household_view(household, grants[household_id], exposures))
    rows.sort(key=lambda r: -max((e["weight"] for e in r["exposures"]), default=0))
    concentrated = [e for r in rows for e in r["exposures"] if e["concentrated"]]
    return {"households": rows, "as_of": today.isoformat(), "threshold": CONCENTRATED, "not_modelled": NOT_MODELLED, "summary": {
        "with_grants": sum(1 for r in rows if r["grants"]), "concentrated": len(concentrated), "no_plan": sum(1 for e in concentrated if not e["plan"]),
        "plan_overdue": sum(1 for r in rows for e in r["exposures"] if e["plan"] and e["plan"]["overdue"]),
        "value_vesting_12m": round(sum(r["value_vesting_12m"] for r in rows), 2)}}


def _schedule(vesting, units: float, grant_date: date) -> list[dict]:
    out = []
    for row in vesting or []:
        try:
            on, amount = date.fromisoformat(str(row["on"])[:10]), float(row["units"])
        except (KeyError, TypeError, ValueError):
            raise EquityCompError("Each vesting line needs a date, YYYY-MM-DD, and a number of units.")
        if amount <= 0 or on < grant_date:
            raise EquityCompError("A vesting line needs units, and cannot come before the grant.")
        out.append({"on": on.isoformat(), "units": amount})
    if sum(v["units"] for v in out) > units + 1e-6:
        raise EquityCompError("The vesting schedule adds up to more than the grant.")
    return sorted(out, key=lambda v: v["on"])


async def prepare_grant(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, employer: str | None, units, grant_date: date | None,
                        source_document: str | None, symbol: str | None = None, strike=None, vesting=None, expires_on: date | None = None) -> dict:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id or person.household_id is None:
        raise EquityCompError("Person not found.")
    if kind not in GRANT_KINDS:
        raise EquityCompError(f"The kind must be one of {', '.join(GRANT_KINDS)}.")
    named, firm_name = (source_document or "").strip(), (employer or "").strip()
    if len(named) < 5:
        raise EquityCompError("Say which plan statement this comes from.")
    if len(firm_name) < 2:
        raise EquityCompError("Give the employer.")
    try:
        amount = float(units)
    except (TypeError, ValueError):
        raise EquityCompError("Units must be a number.")
    if amount <= 0:
        raise EquityCompError("Units must be above zero.")
    if grant_date is None:
        raise EquityCompError("Give the grant date.")
    firm = await session.get(Firm, firm_id)
    if grant_date > firm_time.today(firm):
        raise EquityCompError("A grant cannot be dated in the future.")
    if kind in OPTION_KINDS:
        try:
            price = float(strike)
        except (TypeError, ValueError):
            raise EquityCompError("An option needs its strike price.")
        if price <= 0:
            raise EquityCompError("An option needs its strike price.")
    else:
        price = None
    if expires_on is not None and expires_on <= grant_date:
        raise EquityCompError("It cannot expire before it was granted.")
    ticker = (symbol or "").strip().upper() or None
    return {"person": person, "kind": kind, "employer": firm_name, "units": amount, "strike": price, "grant_date": grant_date, "expires_on": expires_on,
            "document": named, "symbol": ticker, "vesting": _schedule(vesting, amount, grant_date)}


async def record_grant(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> EquityGrant:
    ready = await prepare_grant(session, person_id, firm_id=firm_id, **terms)
    row = EquityGrant(firm_id=firm_id, household_id=ready["person"].household_id, person_id=person_id, kind=ready["kind"], employer=ready["employer"], symbol=ready["symbol"],
                      units=ready["units"], strike=ready["strike"], grant_date=ready["grant_date"], vesting=ready["vesting"], expires_on=ready["expires_on"],
                      source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="equity_grant_recorded", actor=by, content={
        "grant_id": str(row.id), "person_id": str(person_id), "kind": row.kind, "units": ready["units"], "document": row.source_document})
    return row


async def prepare_end(session: AsyncSession, grant_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    row = await session.get(EquityGrant, grant_id)
    if row is None or row.firm_id != firm_id:
        raise EquityCompError("Grant not found.")
    if row.ended_on is not None:
        raise EquityCompError("This grant has already ended.")
    why = (reason or "").strip()
    if len(why) < 3:
        raise EquityCompError("Say why it has ended: exercised, sold, forfeited or lapsed.")
    return {"grant": row, "reason": why, "person": await session.get(Person, row.person_id)}


async def end_grant(session: AsyncSession, grant_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, reason: str | None) -> EquityGrant:
    ready = await prepare_end(session, grant_id, firm_id=firm_id, reason=reason)
    row = ready["grant"]
    row.ended_on, row.ended_reason = firm_time.today(await session.get(Firm, firm_id)), ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="equity_grant_ended", actor=by, content={"grant_id": str(row.id), "reason": row.ended_reason})
    return row


async def prepare_plan(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, symbol: str | None, method: str | None, target_weight, target_date: date | None,
                       rationale: str | None, source_document: str | None) -> dict:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise EquityCompError("Household not found.")
    ticker = (symbol or "").strip().upper()
    if not ticker:
        raise EquityCompError("Say which position this is for.")
    if method not in PLAN_METHODS:
        raise EquityCompError(f"The method must be one of {', '.join(PLAN_METHODS)}.")
    try:
        target = float(target_weight)
    except (TypeError, ValueError):
        raise EquityCompError("The target must be a number.")
    if not 0 <= target < 1:
        raise EquityCompError("The target must be between 0% and 100%.")
    firm = await session.get(Firm, firm_id)
    if target_date is None or target_date <= firm_time.today(firm):
        raise EquityCompError("Give a target date in the future.")
    why, named = (rationale or "").strip(), (source_document or "").strip()
    if len(why) < 5:
        raise EquityCompError("Say why this plan was chosen.")
    if len(named) < 5:
        raise EquityCompError("Say where this was agreed: the meeting note or letter.")
    view = await for_household(session, household_id, firm_id=firm_id)
    now = next((e for e in view["exposures"] if e["symbol"] == ticker), None)
    return {"household": household, "symbol": ticker, "method": method, "target": target, "day": target_date, "rationale": why, "document": named, "now": now}


async def record_plan(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> ConcentrationPlan:
    ready = await prepare_plan(session, household_id, firm_id=firm_id, **terms)
    row = ConcentrationPlan(firm_id=firm_id, household_id=household_id, symbol=ready["symbol"], method=ready["method"], target_weight=ready["target"],
                            target_date=ready["day"], rationale=ready["rationale"], source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="concentration_plan_recorded", actor=by, content={
        "household_id": str(household_id), "symbol": row.symbol, "method": row.method, "target_weight": ready["target"], "document": row.source_document})
    return row
