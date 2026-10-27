"""Billing calculation (L100 §9, L200 §2.1 Track A step 2).

`fees.compute_annual_fee` answers "what does this schedule charge on this much money for
a year". This module answers the question a bill needs: what is due for THIS period, on
THESE accounts, for THIS client - and what could make that number wrong.

  period      monthly / quarterly / annual calendar periods. Arrears bills the last completed
              period, advance bills the one that contains the billing date.
  proration   a mandate in force for part of a period is billed for that part only.
  valuation   cash plus holdings at the latest prices, taken on the billing date. There is no
              balance history to take a period-end or average balance from (no deposits or
              withdrawals are recorded), so this is a snapshot and says so: every bill carries
              the date it was valued on and how old its oldest price is.
  householding mandates in one household on the same schedule are aggregated so the
              breakpoint is reached on the combined balance, and the fee is shared back
              by value (marginal bands, per `compute_annual_fee`).

Every bill carries `checks`: things that make it unsafe or worth a second look. A `block`
stops it being issued unless an approver knowingly overrides it; a `warn` is shown with it.
Nothing here writes: `preview` is a dry run, the billing run (slice 2) persists.
"""
from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import fees as fees_core
from app.aurea_core.pricing import PRIVATE_MAX_STALE_DAYS, price_basis
from app.aurea_core.valuation import latest_prices
from app.core.db import utcnow
from app.models.billing import ARRANGEMENT_SOURCES, FeeArrangement
from app.models.enums import MarketType
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.portfolio import Holding, Instrument

PERIODS_PER_YEAR = {"monthly": 12, "quarterly": 4, "annually": 1}
# Monthly closes are the price cadence here, so a month is normal; much older is not.
MAX_STALE_DAYS = 35
# A snapshot taken this far from the period boundary is no longer a fair stand-in for it.
MAX_BASIS_GAP_DAYS = 7


def _period_containing(frequency: str, d: date) -> tuple[date, date]:
    if frequency == "monthly":
        return date(d.year, d.month, 1), date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    if frequency == "quarterly":
        q = (d.month - 1) // 3
        last = q * 3 + 3
        return date(d.year, q * 3 + 1, 1), date(d.year, last, calendar.monthrange(d.year, last)[1])
    return date(d.year, 1, 1), date(d.year, 12, 31)


def billing_period(frequency: str, method: str, as_of: date) -> tuple[date, date]:
    """The period a bill raised on `as_of` covers."""
    start, end = _period_containing(frequency, as_of)
    if method == "advance":
        return start, end
    return _period_containing(frequency, start - timedelta(days=1))


def period_fraction(effective_from: date, ended_on: date | None, start: date, end: date) -> float:
    """The share of the period the arrangement was in force."""
    lo = max(start, effective_from)
    hi = min(end, ended_on) if ended_on else end
    if hi < lo:
        return 0.0
    return ((hi - lo).days + 1) / ((end - start).days + 1)


async def activate_arrangement(
    session: AsyncSession, *, firm_id: uuid.UUID, mandate: Mandate, fee_schedule_id: uuid.UUID,
    billing_method: str, billing_frequency: str, householding: bool, effective_from: date,
    set_by: str | None, set_at, confirmed_by: str | None, confirmed_at,
    source: str = "onboarding", notes: str | None = None, agreed_billable_aum: float | None = None,
) -> FeeArrangement:
    """Make these the mandate's fee terms, ending whatever applied before."""
    if billing_method not in fees_core.BILLING_METHODS:
        raise ValueError(f"billing_method must be one of {sorted(fees_core.BILLING_METHODS)}")
    if billing_frequency not in fees_core.BILLING_FREQUENCIES:
        raise ValueError(f"billing_frequency must be one of {sorted(fees_core.BILLING_FREQUENCIES)}")
    if source not in ARRANGEMENT_SOURCES:
        raise ValueError(f"source must be one of {ARRANGEMENT_SOURCES}")

    for old in (await session.execute(
        select(FeeArrangement).where(FeeArrangement.mandate_id == mandate.id, FeeArrangement.status == "active")
    )).scalars().all():
        old.status, old.ended_on = "ended", effective_from - timedelta(days=1)

    arrangement = FeeArrangement(
        firm_id=firm_id, mandate_id=mandate.id, fee_schedule_id=fee_schedule_id,
        billing_method=billing_method, billing_frequency=billing_frequency,
        householding=householding, effective_from=effective_from, status="active", source=source,
        agreed_billable_aum=agreed_billable_aum, set_by=set_by, set_at=set_at, confirmed_by=confirmed_by, confirmed_at=confirmed_at, notes=notes,
    )
    session.add(arrangement)
    # The client-facing fee panel reads the schedule off the mandate; keep the two in step.
    mandate.fee_schedule_id = fee_schedule_id
    await session.flush()
    return arrangement


async def _valuations(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID]) -> dict:
    if not account_ids:
        return {}
    accounts = {a.id: a for a in (await session.execute(
        select(Account).where(Account.firm_id == firm_id, Account.id.in_(account_ids))
    )).scalars().all()}
    holdings = (await session.execute(
        select(Holding).where(Holding.firm_id == firm_id, Holding.account_id.in_(account_ids))
    )).scalars().all()
    instruments = {i.id: i for i in (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id)
    )).scalars().all()}
    prices = await latest_prices(session, firm_id)

    out: dict[uuid.UUID, dict] = {}
    for aid, acc in accounts.items():
        out[aid] = {"account": acc, "cash": float(acc.cash_balance or 0), "securities": 0.0,
                    "price_dates": [], "priced": [], "synthetic": set(), "declared": set(), "unpriced": set()}
    for h in holdings:
        v = out[h.account_id]
        mv = float(h.market_value or 0)
        v["securities"] += mv
        inst = instruments.get(h.instrument_id)
        sym = inst.symbol if inst else str(h.instrument_id)[:8]
        price = prices.get(h.instrument_id)
        if price is None:
            if mv > 0:
                v["unpriced"].add(sym)
        else:
            v["price_dates"].append(price.as_of)
            # A private fund is current for about a quarter - its manager states a value quarterly -
            # where a listed price is stale after a month.
            private = bool(inst and inst.market_type == MarketType.PRIVATE)
            v["priced"].append((sym, price.as_of, PRIVATE_MAX_STALE_DAYS if private else MAX_STALE_DAYS))
            basis = price_basis(price.source, price.is_real)
            if basis == "synthetic":
                v["synthetic"].add(sym)
            elif basis == "declared":
                v["declared"].add(sym)
    for v in out.values():
        v["value"] = v["cash"] + v["securities"]
    return out


async def _mandate_households(session: AsyncSession, firm_id: uuid.UUID, mandates: list[Mandate]) -> dict:
    person_ids = [m.person_id for m in mandates if m.person_id]
    entity_ids = [m.entity_id for m in mandates if m.entity_id]
    people = {p.id: p.household_id for p in (await session.execute(
        select(Person).where(Person.firm_id == firm_id, Person.id.in_(person_ids)))).scalars().all()} if person_ids else {}
    entities = {e.id: e.household_id for e in (await session.execute(
        select(LegalEntity).where(LegalEntity.firm_id == firm_id, LegalEntity.id.in_(entity_ids)))).scalars().all()} if entity_ids else {}
    return {m.id: (people.get(m.person_id) if m.person_id else entities.get(m.entity_id)) for m in mandates}


def _check(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail}


async def preview(session: AsyncSession, firm_id: uuid.UUID, *, as_of: date | None = None) -> dict:
    """What a billing run on `as_of` would charge, and what is wrong with it. Writes nothing."""
    as_of = as_of or utcnow().date()

    arrangements = (await session.execute(
        select(FeeArrangement).where(FeeArrangement.firm_id == firm_id, FeeArrangement.status == "active")
    )).scalars().all()
    confirmed = [a for a in arrangements if a.is_confirmed]
    pending = [a for a in arrangements if not a.is_confirmed]

    mandates = {m.id: m for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True))
    )).scalars().all()}
    accounts_by_mandate: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for a in (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all():
        if a.mandate_id:
            accounts_by_mandate[a.mandate_id].append(a.id)
    valuation = await _valuations(session, firm_id, [i for ids in accounts_by_mandate.values() for i in ids])
    household_of = await _mandate_households(session, firm_id, list(mandates.values()))
    household_names = {h.id: h.name for h in (await session.execute(
        select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    schedules = {s.id: s for s in (await session.execute(
        select(FeeSchedule).where(FeeSchedule.firm_id == firm_id))).scalars().all()}

    # One group per householded set (same household, schedule, cadence and period); everyone
    # else is a group of one.
    groups: dict[tuple, list[FeeArrangement]] = defaultdict(list)
    periods: dict[uuid.UUID, tuple[date, date]] = {}
    for arr in confirmed:
        if arr.mandate_id not in mandates:
            continue
        periods[arr.id] = billing_period(arr.billing_frequency, arr.billing_method, as_of)
        hh = household_of.get(arr.mandate_id)
        if arr.householding and hh:
            groups[("hh", hh, arr.fee_schedule_id, arr.billing_frequency, arr.billing_method, *periods[arr.id])].append(arr)
        else:
            groups[("solo", arr.id)].append(arr)

    entries: list[dict] = []
    for members in groups.values():
        schedule = schedules.get(members[0].fee_schedule_id)
        if schedule is None:  # the FK is RESTRICT, so this is a cross-firm id, never a real arrangement
            continue
        rows = []
        for arr in members:
            start, end = periods[arr.id]
            accts = [valuation[i] for i in accounts_by_mandate.get(arr.mandate_id, []) if i in valuation]
            rows.append({
                "arr": arr, "start": start, "end": end, "accts": accts,
                "value": sum(a["value"] for a in accts),
                "fraction": period_fraction(arr.effective_from, arr.ended_on, start, end),
                "checks": [],
            })

        for r in rows:
            currencies = {(a["account"].currency or schedule.currency) for a in r["accts"]}
            if schedule and any(c != schedule.currency for c in currencies):
                r["checks"].append(_check(
                    "currency_mismatch", "block",
                    f"Accounts are in {', '.join(sorted(currencies))} but the schedule bills in {schedule.currency}; "
                    "no conversion is applied."))
            if r["value"] <= 0:
                r["checks"].append(_check("no_billable_value", "block", "The accounts hold no value to bill on."))
            if r["fraction"] <= 0:
                r["checks"].append(_check("not_in_force", "block", "The arrangement was not in force in this period."))

        billable = [r for r in rows if not any(c["severity"] == "block" and c["code"] in
                                                ("currency_mismatch", "no_billable_value", "not_in_force")
                                                for c in r["checks"])]
        group_value = sum(r["value"] for r in billable)
        calc = fees_core.compute_annual_fee(schedule, group_value) if schedule and group_value > 0 else None
        ppy = PERIODS_PER_YEAR[members[0].billing_frequency]

        unrounded: dict[uuid.UUID, float] = {}
        if calc and calc["annual_fee"] is not None:
            for r in billable:
                share = r["value"] / group_value
                unrounded[r["arr"].id] = calc["annual_fee"] * share / ppy * r["fraction"]
        amounts = {k: round(v, 2) for k, v in unrounded.items()}
        if len(amounts) > 1:  # rounding must not lose or invent a cent across the group
            drift = round(round(sum(unrounded.values()), 2) - sum(amounts.values()), 2)
            if drift:
                largest = max(amounts, key=lambda k: amounts[k])
                amounts[largest] = round(amounts[largest] + drift, 2)

        for r in rows:
            arr = r["arr"]
            mandate = mandates[arr.mandate_id]
            hh = household_of.get(arr.mandate_id)
            price_dates = [d for a in r["accts"] for d in a["price_dates"]]
            oldest = min(price_dates) if price_dates else None
            stale = (as_of - oldest).days if oldest else None
            synthetic = sorted(set().union(*[a["synthetic"] for a in r["accts"]])) if r["accts"] else []
            declared = sorted(set().union(*[a["declared"] for a in r["accts"]])) if r["accts"] else []
            unpriced = sorted(set().union(*[a["unpriced"] for a in r["accts"]])) if r["accts"] else []

            checks = list(r["checks"])
            # Each price is judged against ITS limit. The worst offender is named, so the block
            # says which holding is out of date rather than only that something is.
            overdue = sorted(((as_of - d).days - limit, sym, d, limit)
                             for a in r["accts"] for sym, d, limit in a["priced"] if (as_of - d).days > limit)
            if overdue:
                _excess, sym, d, limit = overdue[-1]
                checks.append(_check("stale_valuation", "block",
                                     f"{sym}'s price is {(as_of - d).days} days old (as of {d.isoformat()}); "
                                     f"the limit for it is {limit} days."))
            if declared:
                checks.append(_check("declared_valuation", "warn",
                                     "Valued on a stated valuation, not a market price: " + ", ".join(declared)))
            if synthetic:
                checks.append(_check("synthetic_price", "warn", "Valued on a synthetic price: " + ", ".join(synthetic)))
            if unpriced:
                checks.append(_check("unpriced_holdings", "warn",
                                     "No price on file, valued at the recorded amount: " + ", ".join(unpriced)))
            boundary = r["end"] if arr.billing_method == "arrears" else r["start"]
            if abs((as_of - boundary).days) > MAX_BASIS_GAP_DAYS:
                checks.append(_check(
                    "valuation_not_at_period_boundary", "warn",
                    f"Valued on {as_of.isoformat()}, {abs((as_of - boundary).days)} days from the "
                    f"{'end' if arr.billing_method == 'arrears' else 'start'} of the period."))
            if calc and calc.get("applied_minimum"):
                checks.append(_check("minimum_fee_applied", "info",
                                     "The schedule's minimum annual fee applies on the billed balance."))

            amount = amounts.get(arr.id, 0.0)
            entries.append({
                "arrangement_id": str(arr.id), "mandate_id": str(arr.mandate_id), "mandate_name": mandate.name,
                "household_id": str(hh) if hh else None, "household_name": household_names.get(hh),
                "schedule": {"id": str(schedule.id), "code": schedule.code, "name": schedule.name,
                             "fee_type": schedule.fee_type, "currency": schedule.currency} if schedule else None,
                "billing_method": arr.billing_method, "billing_frequency": arr.billing_frequency,
                "householded": len(members) > 1,
                "period_start": r["start"].isoformat(), "period_end": r["end"].isoformat(),
                "period_fraction": round(r["fraction"], 6), "periods_per_year": ppy,
                "billable_value": round(r["value"], 2),
                "group_value": round(group_value, 2) if len(members) > 1 else None,
                "annual_fee": calc["annual_fee"] if calc and arr.id in unrounded else None,
                "effective_bps": calc["effective_bps"] if calc and arr.id in unrounded else None,
                "breakdown": calc["breakdown"] if calc and arr.id in unrounded else [],
                "amount": amount,
                "currency": schedule.currency if schedule else None,
                "accounts": [{"account_id": str(a["account"].id), "account_name": a["account"].name,
                              "value": round(a["value"], 2), "cash": round(a["cash"], 2),
                              "securities": round(a["securities"], 2)} for a in r["accts"]],
                "valuation": {"basis": "snapshot", "basis_date": as_of.isoformat(),
                              "oldest_price_date": oldest.isoformat() if oldest else None, "stale_days": stale,
                              "synthetic_price_symbols": synthetic, "declared_price_symbols": declared,
                              "unpriced_symbols": unpriced},
                "checks": checks,
                "blocked": any(c["severity"] == "block" for c in checks),
            })

    covered = {a.mandate_id for a in confirmed}
    no_agreement = []
    for mid, m in mandates.items():
        if mid in covered:
            continue
        value = sum(valuation[i]["value"] for i in accounts_by_mandate.get(mid, []) if i in valuation)
        if value > 0:
            hh = household_of.get(mid)
            no_agreement.append({"mandate_id": str(mid), "mandate_name": m.name,
                                 "household_id": str(hh) if hh else None,
                                 "household_name": household_names.get(hh), "value": round(value, 2),
                                 "pending_confirmation": any(p.mandate_id == mid for p in pending)})

    by_currency: dict[str, float] = defaultdict(float)
    for e in entries:
        if not e["blocked"] and e["currency"]:
            by_currency[e["currency"]] += e["amount"]
    entries.sort(key=lambda e: (e["household_name"] or "", e["mandate_name"]))
    no_agreement.sort(key=lambda e: e["household_name"] or e["mandate_name"])
    return {
        "as_of": as_of.isoformat(), "arrangements": entries, "no_agreement": no_agreement,
        "totals": {"bills": len(entries), "blocked": sum(1 for e in entries if e["blocked"]),
                   "amount_by_currency": {k: round(v, 2) for k, v in by_currency.items()},
                   "mandates_without_agreement": len(no_agreement)},
    }
