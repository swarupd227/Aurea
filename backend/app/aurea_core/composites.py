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
    """Monthly returns for one account, built the same way the existing firm-wide number
    is (current holdings held constant across the window — no cash-flow adjustment, so
    this is a close-to-close return, not a true time-weighted return with intra-period
    flows), but across every monthly price point on file instead of just the first/last.
    """
    holdings = (await session.execute(select(Holding).where(Holding.account_id == account_id))).scalars().all()
    total_value = sum(float(h.market_value or 0) for h in holdings)
    if not holdings or total_value <= 0:
        return {"periods": [], "returns": [], "account_value": round(total_value, 2)}

    instrument_ids = list({h.instrument_id for h in holdings})
    prices = (await session.execute(
        select(Price).where(Price.instrument_id.in_(instrument_ids)).order_by(Price.as_of.asc())
    )).scalars().all()
    by_instrument: dict[uuid.UUID, dict] = {}
    for p in prices:
        by_instrument.setdefault(p.instrument_id, {})[p.as_of] = float(p.close)

    # Only instruments with at least 2 price points can contribute a return at all — an
    # instrument with a single (or no) price point is weighted in the account but treated
    # as flat for every period, the same fallback analytics/portfolio.py uses.
    date_sets = [set(closes) for closes in by_instrument.values() if len(closes) >= 2]
    if not date_sets:
        return {"periods": [], "returns": [], "account_value": round(total_value, 2)}
    common_dates = sorted(set.intersection(*date_sets)) if len(date_sets) > 1 else sorted(date_sets[0])
    if len(common_dates) < 2:
        return {"periods": [], "returns": [], "account_value": round(total_value, 2)}

    weights = {h.instrument_id: float(h.market_value or 0) / total_value for h in holdings}

    returns = []
    for i in range(1, len(common_dates)):
        prev_d, cur_d = common_dates[i - 1], common_dates[i]
        period_return = 0.0
        for h in holdings:
            closes = by_instrument.get(h.instrument_id, {})
            if prev_d in closes and cur_d in closes and closes[prev_d] > 0:
                inst_return = (closes[cur_d] - closes[prev_d]) / closes[prev_d]
                period_return += weights.get(h.instrument_id, 0.0) * inst_return
        returns.append(period_return)

    return {
        "periods": [d.isoformat() for d in common_dates[1:]],
        "returns": [round(r, 6) for r in returns],
        "account_value": round(total_value, 2),
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


async def composite_report(session: AsyncSession, composite: Composite) -> dict:
    model = await session.get(ModelPortfolio, composite.model_portfolio_id)
    mandates = (await session.execute(
        select(Mandate).where(
            Mandate.firm_id == composite.firm_id, Mandate.model_portfolio_id == composite.model_portfolio_id
        )
    )).scalars().all()
    mandate_ids = [m.id for m in mandates]
    accounts = (
        await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
    ).scalars().all() if mandate_ids else []

    now = utcnow()
    seasoning_cutoff = now - timedelta(days=composite.seasoning_days)
    qualifying, excluded_unseasoned, excluded_small = [], [], []
    for a in accounts:
        value = float(a.cash_balance or 0) + sum(
            float(h.market_value or 0) for h in
            (await session.execute(select(Holding).where(Holding.account_id == a.id))).scalars().all()
        )
        if composite.minimum_account_size and value < float(composite.minimum_account_size):
            excluded_small.append(a.name)
            continue
        created = _aware(a.created_at)
        if created is not None and created > seasoning_cutoff:
            excluded_unseasoned.append(a.name)
            continue
        qualifying.append(a)

    per_account = []
    for a in qualifying:
        series = await account_return_series(session, a.id)
        per_account.append({"account_id": str(a.id), "account_name": a.name, **series})

    latest = [(p["account_value"], p["returns"][-1]) for p in per_account if p["returns"]]
    composite_assets = sum(v for v, _ in latest)
    gross_return = round(sum(v * r for v, r in latest) / composite_assets, 4) if composite_assets else None

    dispersion = None
    if len(latest) >= 2 and composite_assets and gross_return is not None:
        variance = sum(v * (r - gross_return) ** 2 for v, r in latest) / composite_assets
        dispersion = round(variance ** 0.5, 4)

    series = _composite_series(per_account)
    months_used = len(series)
    ex_post_std_dev = None
    if months_used >= 2:
        monthly_std = statistics.pstdev(series)
        ex_post_std_dev = round(monthly_std * (12 ** 0.5), 4)

    benchmark = None
    if model and model.benchmark_symbol:
        bm_prices = (await session.execute(
            select(Price).where(Price.firm_id == composite.firm_id).order_by(Price.as_of.asc())
        )).scalars().all()
        # Matched by symbol via a join would need the Instrument row; a composite report
        # is infrequent enough that a targeted lookup is clearer than pre-joining above.
        from app.models.portfolio import Instrument
        inst = (await session.execute(
            select(Instrument).where(Instrument.firm_id == composite.firm_id, Instrument.symbol == model.benchmark_symbol)
        )).scalar_one_or_none()
        if inst:
            bm_points = [p for p in bm_prices if p.instrument_id == inst.id]
            if len(bm_points) >= 2 and float(bm_points[0].close) > 0:
                benchmark = {
                    "symbol": model.benchmark_symbol,
                    "return": round((float(bm_points[-1].close) - float(bm_points[0].close)) / float(bm_points[0].close), 4),
                }

    return {
        "composite_id": str(composite.id), "name": composite.name,
        "strategy": model.name if model else None,
        "inclusion_criteria": composite.inclusion_criteria,
        "creation_date": composite.creation_date.isoformat(),
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
