"""GIPS composite construction and reporting (L200-5 §2.5).

Two genuinely new capabilities this needed, neither of which existed anywhere in the
platform before this module: a return SERIES for one account (the existing book-level
number in `analytics/portfolio.py` only ever compares the earliest and latest price on
file — one number, firm-wide), and asset-weighted dispersion across accounts running the
same strategy.

Honesty constraint, stated once here because it shapes every number below: a seeded demo
firm carries roughly a year of monthly price history, not the three years GIPS composites
conventionally present a standard deviation over. Rather than compute a "3-year" figure
from twelve months of data, `ex_post_std_dev` reports exactly how many months it used and
whether that meets the GIPS minimum — the same disclosure discipline the platform already
applies to a synthetic price (`price_source: "synthetic"`), applied to a data-depth
shortfall instead of a pricing one.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.composites import Composite
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, ModelPortfolio, Price

GIPS_MIN_MONTHS = 36


def _aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def account_return_series(session: AsyncSession, account_id: uuid.UUID) -> dict:
    """Monthly returns for one account, from the same engine every other performance
    figure uses (`performance.py`): positions rebuilt at each month-end from the ledger,
    trades treated as flows, months linked. This used to be a second, cruder method -
    today's holdings weighted by today's value, held constant across the window - which
    meant a composite and a household could report different returns for the same money.
    """
    from app.aurea_core import performance

    account = await session.get(Account, account_id)
    if account is None:
        return {"periods": [], "returns": [], "account_value": 0.0, "start": None, "end": None}
    perf = await performance.account_performance(session, account_id, firm_id=account.firm_id)
    return {
        "periods": [p["end"] for p in perf["periods"]],
        "returns": [p["return"] for p in perf["periods"]],
        "account_value": perf["account_value"],
        "start": perf["start"], "end": perf["end"],
    }


def _composite_series(per_account: list[dict]) -> list[float]:
    """Asset-weighted composite return per period, aligned by period label across
    accounts whose own return series may differ in length or dates (different accounts
    can hold different instruments with different price histories) — weighted each
    period only by the accounts that actually have a return for it."""
    by_period: dict[str, list[tuple[float, float]]] = {}
    for acc in per_account:
        for period, ret in zip(acc["periods"], acc["returns"]):
            by_period.setdefault(period, []).append((acc["account_value"], ret))

    series = []
    for period in sorted(by_period):
        contributions = by_period[period]
        total = sum(v for v, _ in contributions)
        if total > 0:
            series.append(sum(v * r for v, r in contributions) / total)
    return series


BENCHMARK_CHANGED = "benchmark_changed"


async def record_benchmark_change(
    session: AsyncSession, model: ModelPortfolio, *, to: str | None, actor: str, reason: str,
) -> bool:
    """Set a model's benchmark and put the change on the ledger, which is where a composite's
    GIPS disclosure of it is read from. Every path that changes a benchmark goes through here.
    Returns False when nothing changed."""
    from app.provenance import ledger

    before = model.benchmark_symbol or None
    to = to or None
    if to == before:
        return False
    model.benchmark_symbol = to
    await ledger.append_entry(
        session, firm_id=model.firm_id, event_type=BENCHMARK_CHANGED, actor=actor,
        content={"model_id": str(model.id), "model": model.name, "from": before, "to": to, "reason": reason})
    return True


async def benchmark_changes(session: AsyncSession, model: ModelPortfolio) -> list[dict]:
    """Every change to this model's benchmark, oldest first: date, from, to, why, by whom.
    GIPS requires a composite presentation to disclose a benchmark change, its date and the
    reason. Entries written before the model id was recorded are matched by the model's name."""
    from app.aurea_core.performance import benchmark_label
    from app.models.governance import LedgerEntry

    rows = (await session.execute(
        select(LedgerEntry).where(LedgerEntry.firm_id == model.firm_id,
                                  LedgerEntry.event_type == BENCHMARK_CHANGED)
        .order_by(LedgerEntry.seq.asc())
    )).scalars().all()
    out = []
    for e in rows:
        c = e.content or {}
        if c.get("model_id") != str(model.id) and not (c.get("model_id") is None and c.get("model") == model.name):
            continue
        out.append({
            "date": e.created_at.date().isoformat() if e.created_at else None,
            "from": c.get("from"), "from_label": benchmark_label(c.get("from")) or "none",
            "to": c.get("to"), "to_label": benchmark_label(c.get("to")) or "none",
            "reason": c.get("reason"), "by": e.actor,
        })
    return out


async def composite_report(session: AsyncSession, composite: Composite) -> dict:
    from app.aurea_core import performance
    from app.models.enums import MandateType

    model = await session.get(ModelPortfolio, composite.model_portfolio_id)
    # Only discretionary mandates: an advisory account on the same model is the client's own
    # decisions, which GIPS keeps out of a composite - and out of the firm definition check.
    mandates = (await session.execute(
        select(Mandate).where(
            Mandate.firm_id == composite.firm_id, Mandate.model_portfolio_id == composite.model_portfolio_id,
            Mandate.mandate_type == MandateType.DISCRETIONARY,
        )
    )).scalars().all()
    mandate_ids = [m.id for m in mandates]
    accounts = (
        await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
    ).scalars().all() if mandate_ids else []

    now = utcnow()
    seasoning_cutoff = now - timedelta(days=composite.seasoning_days)
    # Seasoning is for an account that joins after the composite was built. One the firm already
    # managed when it was built is part of the back-book the composite was built from. The
    # platform records when an account was opened, not when its mandate moved onto this model,
    # so an older account moved onto the model later is counted as back-book.
    constructed = _aware(composite.created_at) or now
    qualifying, excluded_unseasoned, excluded_small = [], [], []
    value_of: dict[uuid.UUID, float] = {}
    for a in accounts:
        value = float(a.cash_balance or 0) + sum(
            float(h.market_value or 0) for h in
            (await session.execute(select(Holding).where(Holding.account_id == a.id))).scalars().all()
        )
        if composite.minimum_account_size and value < float(composite.minimum_account_size):
            excluded_small.append(a.name)
            continue
        created = _aware(a.created_at)
        if created is not None and created > constructed and created > seasoning_cutoff:
            excluded_unseasoned.append(a.name)
            continue
        qualifying.append(a)
        value_of[a.id] = value

    per_account = []
    for a in qualifying:
        series = await account_return_series(session, a.id)
        per_account.append({"account_id": str(a.id), "account_name": a.name, **series})

    # Composite assets are valued as the firm definition check values them (holdings and cash),
    # so the composites can be read against the firm's discretionary total. Returns are weighted
    # by the invested value each one was measured on.
    composite_assets = sum(value_of[a.id] for a in qualifying)
    latest = [(p["account_value"], p["returns"][-1]) for p in per_account if p["returns"]]
    weight = sum(v for v, _ in latest)
    gross_return = round(sum(v * r for v, r in latest) / weight, 4) if weight else None

    dispersion = None
    if len(latest) >= 2 and weight and gross_return is not None:
        variance = sum(v * (r - gross_return) ** 2 for v, r in latest) / weight
        dispersion = round(variance ** 0.5, 4)

    series = _composite_series(per_account)
    months_used = len(series)
    ex_post_std_dev = None
    if months_used >= 2:
        monthly_std = statistics.pstdev(series)
        ex_post_std_dev = round(monthly_std * (12 ** 0.5), 4)

    # The benchmark is measured over exactly the window the composite was, not over whatever
    # price history happens to be stored (which now grows daily).
    benchmark = None
    if model and model.benchmark_symbol:
        starts = [p["start"] for p in per_account if p.get("start")]
        ends = [p["end"] for p in per_account if p.get("end")]
        if starts and ends:
            bm = await performance.benchmark_return(
                session, composite.firm_id, model.benchmark_symbol, min(starts), max(ends))
            if bm is not None:
                benchmark = {"symbol": model.benchmark_symbol, "return": bm}
    # What the benchmark is and how it changed are disclosed whether or not it could be measured.
    benchmark_disclosure = {
        "symbol": model.benchmark_symbol if model else None,
        "label": performance.benchmark_label(model.benchmark_symbol) if model else None,
        "basis": ("fixed-weight blend, weights set at the start of the period, not rebalanced"
                  if model and len(performance.parse_benchmark(model.benchmark_symbol)) > 1 else None),
        "measured": benchmark is not None,
        "changes": await benchmark_changes(session, model) if model else [],
    }

    return {
        "composite_id": str(composite.id), "name": composite.name,
        "strategy": model.name if model else None,
        "inclusion_criteria": composite.inclusion_criteria,
        "creation_date": composite.creation_date.isoformat(),
        "seasoning_days": composite.seasoning_days,
        "constructed_on": constructed.date().isoformat(),
        "accounts_included": len(qualifying),
        "accounts_excluded_unseasoned": excluded_unseasoned,
        "accounts_excluded_below_minimum": excluded_small,
        "composite_assets": round(composite_assets, 2),
        "gross_return": gross_return,
        "net_return": None,  # fee data is not reliably linked per account — reported honestly absent, not estimated
        "internal_dispersion": dispersion,
        "ex_post_std_dev": {
            "value": ex_post_std_dev, "months_used": months_used,
            "months_required_for_gips": GIPS_MIN_MONTHS,
            "meets_gips_minimum": months_used >= GIPS_MIN_MONTHS,
        },
        "benchmark": benchmark,
        "benchmark_disclosure": benchmark_disclosure,
        "accounts": per_account,
    }


async def firm_definition_check(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """GIPS's "foundational sin" per L200-5 §2.5: the claimed firm must be all
    discretionary fee-paying assets, not a narrowed subset chosen to exclude bad history.
    Compares total discretionary AUM against AUM actually captured in an active
    composite."""
    from app.models.enums import MandateType

    mandates = (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, Mandate.mandate_type == MandateType.DISCRETIONARY)
    )).scalars().all()
    mandate_ids = {m.id for m in mandates}
    if not mandate_ids:
        return {"discretionary_aum": 0.0, "composite_aum": 0.0, "uncaptured_aum": 0.0, "clean": True}

    accounts = (
        await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
    ).scalars().all()

    modeled_mandate_ids = {m.id for m in mandates if m.model_portfolio_id is not None}
    discretionary_aum = captured_aum = 0.0
    for a in accounts:
        value = float(a.cash_balance or 0) + sum(
            float(h.market_value or 0) for h in
            (await session.execute(select(Holding).where(Holding.account_id == a.id))).scalars().all()
        )
        discretionary_aum += value
        if a.mandate_id in modeled_mandate_ids:
            captured_aum += value

    uncaptured = round(discretionary_aum - captured_aum, 2)
    return {
        "discretionary_aum": round(discretionary_aum, 2),
        "composite_eligible_aum": round(captured_aum, 2),
        "uncaptured_aum": uncaptured,
        "clean": uncaptured < 0.01,
    }
