"""Portfolio performance measurement (L100 §9, L200-5): a real time-weighted return, a
money-weighted return, and attribution that adds up to the headline.

What this replaces: the firm "TWR" was `(value of current holdings today - value of those
same holdings at the earliest stored price) / the latter`. Current quantities held
constant across the window, no sub-period linking, no trades, no income - survivorship-
biased by construction, and labelled time-weighted when it was not.

Method (stated once, because it shapes every number): the position in each instrument at
every month-end is rebuilt by rolling the order/dividend ledger backward from today's
holding, each month's return is `(V_end - V_start - flows + income) / (V_start + weighted
flows)` (Modified Dietz, flows weighted by days remaining), and the months are geometrically
linked. A buy is a flow INTO the securities sleeve, a sale a flow OUT of it.

What it cannot do, and says so in `disclosures` rather than papering over:
  - Securities only. Cash is excluded: the platform records no cash-balance history.
  - No external cash flows. Deposits and withdrawals are not recorded anywhere, so a
    client's own contributions cannot be separated from performance beyond what trades show.
  - Gross of fees. Fee data is not reliably linked per account.
  - Prices are monthly closes. A synthetic price is named, an instrument with no price
    history is held flat and named, and the window ends at the last price on file.
"""
from __future__ import annotations

import uuid
from bisect import bisect_left
from collections import defaultdict
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.pricing import month_end_points, price_basis
from app.core.db import utcnow
from app.models.enums import AssetClass
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import (
    Holding, Instrument, ModelPortfolio, Price, TaxLot, Transaction,
)

_EPS = 1e-9
_SNAP_DAYS = 5   # month-end closes of different exchanges can sit several days apart around holidays
_INCOME_TYPES = {"dividend", "distribution"}


def _f(x) -> float:
    return float(x or 0)


def _empty(account_value: float = 0.0, **disclosure) -> dict:
    return {
        "twr": None, "mwr": None, "start": None, "end": None, "months": 0, "periods": [],
        "start_value": 0.0, "end_value": 0.0, "attribution": [],
        "account_value": round(account_value, 2),
        "disclosures": {"scope": "securities_only", "gross_of_fees": True,
                         "external_cash_flows_recorded": False, "synthetic_price_symbols": [],
                         "declared_price_symbols": [],
                         "unpriced_flat_symbols": [], "ledger_gap_lots": 0, "periods_skipped": 0,
                         "as_of": None, "stale_days": None, **disclosure},
    }


def _irr(cashflows: list[tuple[date, float]]) -> float | None:
    """Annualised internal rate of return by bisection; None when no sign change exists."""
    if len(cashflows) < 2:
        return None
    t0 = cashflows[0][0]

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** ((d - t0).days / 365.0) for d, cf in cashflows)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


async def performance_for_accounts(
    session: AsyncSession, account_ids: list[uuid.UUID], *, firm_id: uuid.UUID
) -> dict:
    """TWR, MWR and attribution for the combined securities held in these accounts."""
    if not account_ids:
        return _empty()

    holdings = (await session.execute(
        select(Holding).where(Holding.firm_id == firm_id, Holding.account_id.in_(account_ids))
    )).scalars().all()
    txns = (await session.execute(
        select(Transaction).where(Transaction.firm_id == firm_id, Transaction.account_id.in_(account_ids))
    )).scalars().all()
    account_value = sum(_f(h.market_value) for h in holdings)

    holding_by_id = {h.id: h for h in holdings}
    lots = (await session.execute(
        select(TaxLot).where(TaxLot.holding_id.in_(list(holding_by_id)))
    )).scalars().all() if holding_by_id else []

    q_now: dict[uuid.UUID, float] = defaultdict(float)
    mv_now: dict[uuid.UUID, float] = defaultdict(float)
    for h in holdings:
        q_now[h.instrument_id] += _f(h.quantity)
        mv_now[h.instrument_id] += _f(h.market_value)

    # Ledger events per instrument: (date, signed quantity, flow into the sleeve).
    events: dict[uuid.UUID, list[tuple[date, float, float]]] = defaultdict(list)
    income: dict[uuid.UUID, list[tuple[date, float]]] = defaultdict(list)
    buy_qty: dict[tuple, float] = defaultdict(float)
    for t in txns:
        if t.instrument_id is None:
            continue
        kind = str(t.txn_type).lower()
        if kind == "buy":
            events[t.instrument_id].append((t.trade_date, _f(t.quantity), _f(t.amount)))
            buy_qty[(t.account_id, t.instrument_id, t.trade_date)] += _f(t.quantity)
        elif kind == "sell":
            events[t.instrument_id].append((t.trade_date, -_f(t.quantity), -_f(t.amount)))
        elif kind in _INCOME_TYPES:
            income[t.instrument_id].append((t.trade_date, _f(t.amount)))
        # Other transaction types (capital calls etc.) are not modelled as sleeve flows.

    # A tax lot with no matching buy in the ledger is a purchase the ledger never recorded.
    # It still moved the position, so it is dated by the lot - and counted in the disclosure.
    lot_qty: dict[tuple, float] = defaultdict(float)
    lot_cost: dict[tuple, float] = defaultdict(float)
    for lot in lots:
        h = holding_by_id[lot.holding_id]
        key = (h.account_id, h.instrument_id, lot.acquired_on)
        lot_qty[key] += _f(lot.quantity)
        lot_cost[key] += _f(lot.quantity) * _f(lot.cost_per_unit)
    gap_dates: list[date] = []
    for key, qty in lot_qty.items():
        gap = qty - buy_qty.get(key, 0.0)
        if gap > 1e-6:
            _, iid, acquired = key
            avg_cost = lot_cost[key] / qty if qty else 0.0
            events[iid].append((acquired, gap, gap * avg_cost))
            gap_dates.append(acquired)

    instrument_ids = set(q_now) | set(events) | set(income)
    instruments = {
        i.id: i for i in (await session.execute(
            select(Instrument).where(Instrument.id.in_(instrument_ids))
        )).scalars().all()
    } if instrument_ids else {}

    prices = (await session.execute(
        select(Price).where(Price.instrument_id.in_(instrument_ids)).order_by(Price.as_of.asc())
    )).scalars().all() if instrument_ids else []
    raw: dict[uuid.UUID, dict[date, float]] = defaultdict(dict)
    basis_on: dict[tuple[uuid.UUID, date], str] = {}
    for p in prices:
        raw[p.instrument_id][p.as_of] = _f(p.close)
        basis_on[(p.instrument_id, p.as_of)] = price_basis(p.source, p.is_real)
    # Prices are stored daily; this engine reads them monthly. Counting days as months would
    # scale every return series and the composites' "months of history" by about twenty.
    closes: dict[uuid.UUID, dict[date, float]] = {iid: month_end_points(pts) for iid, pts in raw.items()}
    # Synthetic only if a point actually USED is - an old synthetic row that real history has
    # since replaced must not brand the instrument forever.
    synthetic: set[uuid.UUID] = {iid for iid, pts in closes.items()
                                 if any(basis_on[(iid, d)] == "synthetic" for d in pts)}
    # A private fund's stated valuation is not a market price, but it is not invented either:
    # it is named for what it is.
    declared: set[uuid.UUID] = {iid for iid, pts in closes.items()
                                if any(basis_on[(iid, d)] == "declared" for d in pts)}

    def symbol(iid) -> str:
        i = instruments.get(iid)
        return i.symbol if i else str(iid)[:8]

    priced = [iid for iid in instrument_ids if len(closes.get(iid, {})) >= 2]
    unpriced = [iid for iid in instrument_ids if iid not in priced and mv_now.get(iid, 0.0) > 0]
    flat_value = {iid: mv_now[iid] for iid in unpriced}

    base_disclosure = {
        "synthetic_price_symbols": sorted(symbol(i) for i in priced if i in synthetic),
        "declared_price_symbols": sorted(symbol(i) for i in priced if i in declared),
        "unpriced_flat_symbols": sorted(symbol(i) for i in unpriced),
        "ledger_gap_lots": 0,
    }
    if not priced:
        return _empty(account_value, **base_disclosure)

    # One common calendar to value every position on. Exchanges stamp a month's first close
    # a day apart (a NZ stock lands on the 31st, a US one on the 1st), so each instrument's
    # close is matched to the nearest date within a few days rather than demanding an exact
    # match - which would leave any household holding a NZ and a US stock unmeasurable.
    sorted_dates = {iid: sorted(closes[iid]) for iid in priced}

    def snap(iid, g: date) -> date | None:
        ds = sorted_dates[iid]
        i = bisect_left(ds, g)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(ds):
                gap = abs((ds[j] - g).days)
                if gap <= _SNAP_DAYS and (best is None or gap < best[0]):
                    best = (gap, ds[j])
        return best[1] if best else None

    # The longest history sets the calendar. (It used to be the most COMMON exact date list,
    # which only worked while instruments shared dates; month-end closes fall on different
    # trading days, so every list is unique and "most common" would pick one arbitrarily.)
    reference = max(sorted_dates.values(), key=lambda ds: (len(ds), ds[-1]))
    grid: list[date] = []
    px: dict[uuid.UUID, dict[date, float]] = {iid: {} for iid in priced}
    for g in reference:
        snapped = {iid: snap(iid, g) for iid in priced}
        if all(v is not None for v in snapped.values()):
            grid.append(g)
            for iid, v in snapped.items():
                px[iid][g] = closes[iid][v]
    if len(grid) < 2:
        return _empty(account_value, **base_disclosure)
    # Only a purchase inside the measured window matters; seed lots predate the ledger by design.
    base_disclosure["ledger_gap_lots"] = sum(1 for d in gap_dates if grid[0] < d <= grid[-1])

    def qty_at(iid, d: date) -> float:
        q = q_now.get(iid, 0.0)
        for ed, sq, _ in events.get(iid, []):
            if ed > d:
                q -= sq
        return max(q, 0.0)

    def asset_class(iid) -> str:
        i = instruments.get(iid)
        return i.asset_class if i and i.asset_class else "multi_asset"

    periods: list[dict] = []
    factor = 1.0
    contribution: dict[str, float] = defaultdict(float)
    class_factor: dict[str, float] = defaultdict(lambda: 1.0)
    mwr_flows: list[tuple[date, float]] = []
    skipped = 0

    for a, b in zip(grid, grid[1:]):
        span = max((b - a).days, 1)
        by_class: dict[str, dict[str, float]] = defaultdict(
            lambda: {"va": 0.0, "vb": 0.0, "f": 0.0, "wf": 0.0, "inc": 0.0})
        period_flows: list[tuple[date, float]] = []
        for iid in priced:
            c = by_class[asset_class(iid)]
            c["va"] += qty_at(iid, a) * px[iid][a]
            c["vb"] += qty_at(iid, b) * px[iid][b]
            for ed, _, flow in events.get(iid, []):
                if a < ed <= b:
                    c["f"] += flow
                    c["wf"] += flow * (b - ed).days / span
                    period_flows.append((ed, flow))
            for idt, amt in income.get(iid, []):
                if a < idt <= b:
                    c["inc"] += amt
                    period_flows.append((idt, -amt))
        for iid in unpriced:
            c = by_class[asset_class(iid)]
            c["va"] += flat_value[iid]
            c["vb"] += flat_value[iid]

        va = sum(c["va"] for c in by_class.values())
        vb = sum(c["vb"] for c in by_class.values())
        f_tot = sum(c["f"] for c in by_class.values())
        wf = sum(c["wf"] for c in by_class.values())
        inc = sum(c["inc"] for c in by_class.values())
        denom = va + wf
        if denom <= _EPS:
            skipped += 1  # no capital invested yet - there is nothing to measure a return on
            continue

        gain = vb - va - f_tot + inc
        r = gain / denom
        for cls, c in by_class.items():
            contribution[cls] += ((c["vb"] - c["va"] - c["f"] + c["inc"]) / denom) * factor
            cdenom = c["va"] + c["wf"]
            if cdenom > _EPS:
                class_factor[cls] *= 1.0 + (c["vb"] - c["va"] - c["f"] + c["inc"]) / cdenom
        factor *= 1.0 + r

        if not mwr_flows:
            mwr_flows.append((a, -va))
        # Investor's view: money into the sleeve is an outflow, proceeds and income come back.
        mwr_flows.extend((d, -flow) for d, flow in period_flows)
        periods.append({"start": a.isoformat(), "end": b.isoformat(),
                        "start_value": round(va, 2), "end_value": round(vb, 2),
                        "net_flow": round(f_tot, 2), "income": round(inc, 2),
                        "return": round(r, 6)})

    if not periods:
        return _empty(account_value, periods_skipped=skipped, **base_disclosure)

    end_date = date.fromisoformat(periods[-1]["end"])
    start_date = date.fromisoformat(periods[0]["start"])
    mwr_flows.append((end_date, periods[-1]["end_value"]))
    mwr_flows.sort(key=lambda x: x[0])
    irr = _irr(mwr_flows)
    mwr = None
    if irr is not None:
        years = max((end_date - start_date).days, 1) / 365.0
        mwr = {"period": round((1.0 + irr) ** years - 1.0, 4), "annualised": round(irr, 4)}

    weights_total = sum(mv_now.values()) or 1.0
    class_mv: dict[str, float] = defaultdict(float)
    for iid, mv in mv_now.items():
        class_mv[asset_class(iid)] += mv
    attribution = [
        {"asset_class": cls, "contribution": round(contribution[cls], 4),
         "class_return": round(class_factor[cls] - 1.0, 4) if cls in class_factor else None,
         "weight": round(class_mv.get(cls, 0.0) / weights_total, 4)}
        for cls in sorted(contribution, key=lambda c: -contribution[c])
    ]

    return {
        "twr": round(factor - 1.0, 4), "mwr": mwr,
        "start": periods[0]["start"], "end": periods[-1]["end"], "months": len(periods),
        "periods": periods, "start_value": periods[0]["start_value"],
        "end_value": periods[-1]["end_value"], "attribution": attribution,
        "account_value": round(account_value, 2),
        "disclosures": {
            "scope": "securities_only", "gross_of_fees": True, "external_cash_flows_recorded": False,
            **base_disclosure, "periods_skipped": skipped, "as_of": periods[-1]["end"],
            "stale_days": (utcnow().date() - end_date).days,
        },
    }


async def account_performance(session: AsyncSession, account_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    return await performance_for_accounts(session, [account_id], firm_id=firm_id)


async def household_account_ids(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[uuid.UUID] | None:
    """Accounts under every person and entity in the household; None if it is not this firm's."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    persons = (await session.execute(
        select(Person).where(Person.firm_id == firm_id, Person.household_id == household_id)
    )).scalars().all()
    entities = (await session.execute(
        select(LegalEntity).where(LegalEntity.firm_id == firm_id, LegalEntity.household_id == household_id)
    )).scalars().all()
    conds = []
    if persons:
        conds.append(Mandate.person_id.in_([p.id for p in persons]))
    if entities:
        conds.append(Mandate.entity_id.in_([e.id for e in entities]))
    if not conds:
        return []
    mandate_ids = [m.id for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, or_(*conds))
    )).scalars().all()]
    if not mandate_ids:
        return []
    return [a.id for a in (await session.execute(
        select(Account).where(Account.firm_id == firm_id, Account.mandate_id.in_(mandate_ids))
    )).scalars().all()]


# ── Benchmarks ────────────────────────────────────────────────────────────────
# A benchmark is one symbol ("SPY") or a fixed-weight blend ("SPY:0.7,AGG:0.3"). A blend's return
# is the weighted sum of its parts' returns over the window: weights set at the start and not
# rebalanced - the same arithmetic as holding the parts from the start date.

GROWTH_PROXY, DEFENSIVE_PROXY = "SPY", "AGG"
_DEFENSIVE = {AssetClass.FIXED_INCOME.value, AssetClass.CASH.value}


def parse_benchmark(spec: str | None) -> list[tuple[str, float]]:
    if not spec:
        return []
    parts = []
    for piece in spec.split(","):
        sym, _, w = piece.strip().partition(":")
        if sym:
            parts.append((sym.strip(), float(w) if w else 1.0))
    total = sum(w for _, w in parts)
    return [(s, w / total) for s, w in parts] if total > 0 else []


def format_benchmark(parts: list[tuple[str, float]]) -> str | None:
    """Merge, round to whole per cent and write a spec back; one part is just its symbol."""
    merged: dict[str, float] = defaultdict(float)
    for sym, w in parts:
        merged[sym] += w
    total = sum(merged.values())
    if total <= 0:
        return None
    pcts = {s: round(100 * w / total) for s, w in merged.items() if round(100 * w / total) > 0}
    if len(pcts) == 1:
        return next(iter(pcts))
    return ",".join(f"{s}:{p / 100:g}" for s, p in sorted(pcts.items(), key=lambda kv: (-kv[1], kv[0])))


def benchmark_label(spec: str | None) -> str | None:
    """How people read a benchmark: "70% SPY / 30% AGG", or just "SPY"."""
    parts = parse_benchmark(spec)
    if len(parts) <= 1:
        return parts[0][0] if parts else None
    return " / ".join(f"{round(w * 100)}% {s}" for s, w in parts)


def blend_for_targets(targets) -> str | None:
    """A model's benchmark from its own targets: its defensive share (fixed income, cash) against a
    bond index, everything else against an equity index - so a 50/30/10/10 model is compared with
    70% SPY / 30% AGG, not with bonds alone."""
    total = sum(float(t.target_weight) for t in targets)
    if total <= 0:
        return None
    defensive = sum(float(t.target_weight) for t in targets if str(t.asset_class) in _DEFENSIVE) / total
    return format_benchmark([(GROWTH_PROXY, 1 - defensive), (DEFENSIVE_PROXY, defensive)])


async def benchmark_return(
    session: AsyncSession, firm_id: uuid.UUID, symbol: str | None, start: str | None, end: str | None
) -> float | None:
    """The benchmark's return over exactly the window the portfolio was measured on. A blend
    with any part unpriced has no return - it is not quietly measured on the parts that are."""
    parts = parse_benchmark(symbol)
    if len(parts) > 1:
        returns = [await _symbol_return(session, firm_id, s, start, end) for s, _ in parts]
        if any(r is None for r in returns):
            return None
        return round(sum(w * r for (_, w), r in zip(parts, returns)), 4)
    return await _symbol_return(session, firm_id, parts[0][0] if parts else None, start, end)


async def _symbol_return(
    session: AsyncSession, firm_id: uuid.UUID, symbol: str | None, start: str | None, end: str | None
) -> float | None:
    if not symbol or not start or not end:
        return None
    inst = (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id, Instrument.symbol == symbol)
    )).scalar_one_or_none()
    if inst is None:
        return None
    rows = (await session.execute(
        select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.asc())
    )).scalars().all()
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)

    def on_or_before(d: date) -> float | None:
        px = None
        for r in rows:
            if r.as_of <= d:
                px = _f(r.close)
            else:
                break
        return px

    p0, p1 = on_or_before(start_d), on_or_before(end_d)
    if not p0 or p1 is None or p0 <= 0:
        return None
    return round(p1 / p0 - 1.0, 4)


async def _household_benchmark(session: AsyncSession, accounts: list[Account]) -> str | None:
    """Each account is held to its own model's benchmark, weighted by what the account is worth
    today. A household with a balanced trust and a growth joint account is compared with the mix
    of the two, not with whichever mandate happened to be read first."""
    parts: list[tuple[str, float]] = []
    for acc in accounts:
        if not acc.mandate_id:
            continue
        mandate = await session.get(Mandate, acc.mandate_id)
        model = await session.get(ModelPortfolio, mandate.model_portfolio_id) if mandate and mandate.model_portfolio_id else None
        if not model or not model.benchmark_symbol:
            continue
        value = sum(_f(h.market_value) or 0.0 for h in (await session.execute(
            select(Holding).where(Holding.account_id == acc.id))).scalars().all())
        if value > 0:
            parts += [(s, w * value) for s, w in parse_benchmark(model.benchmark_symbol)]
    return format_benchmark(parts) if parts else None


async def household_performance(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    account_ids = await household_account_ids(session, household_id, firm_id=firm_id)
    if account_ids is None:
        return None
    household = await session.get(Household, household_id)
    result = await performance_for_accounts(session, account_ids, firm_id=firm_id)
    accounts = (await session.execute(
        select(Account).where(Account.id.in_(account_ids)))).scalars().all() if account_ids else []
    symbol = await _household_benchmark(session, accounts)
    bm = await benchmark_return(session, firm_id, symbol, result["start"], result["end"])
    result.update({
        "household_id": str(household_id), "household_name": household.name,
        "accounts": len(account_ids), "benchmark_symbol": symbol if bm is not None else None,
        "benchmark_label": benchmark_label(symbol) if bm is not None else None,
        # Held to a benchmark that has no price over this window: said, not silently dropped.
        "benchmark_unmeasured": benchmark_label(symbol) if (symbol and bm is None and result["start"]) else None,
        "benchmark_return": bm,
        "alpha": round(result["twr"] - bm, 4) if (bm is not None and result["twr"] is not None) else None,
    })
    return result


async def book_performance(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    account_ids = [a.id for a in (await session.execute(
        select(Account).where(Account.firm_id == firm_id))).scalars().all()]
    return await performance_for_accounts(session, account_ids, firm_id=firm_id)
