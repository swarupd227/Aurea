"""Layer 2.4 — Practice & business analytics (Analytics Companion §2.4).

The board-level view: capacity, cost-to-serve, growth, fee/margin and M&A economics."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.analytics import assumptions as A
from app.aurea_core.analytics._common import gather_brains, positions_of
from app.models.enums import OnboardingStatus, RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.models.onboarding import BookIntegrationBatch, OnboardingCase
from app.models.portfolio import Transaction


async def _billing_position(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """What has really been billed, and what is stuck. None - not zero - when nothing has been
    billed yet: an unmeasured figure is not a figure of nil."""
    from app.models.billing import Invoice
    from app.models.tenant import Firm

    firm = await session.get(Firm, firm_id)
    currency = (firm.base_currency if firm else None) or "NZD"
    rows = (await session.execute(select(Invoice).where(
        Invoice.firm_id == firm_id, Invoice.currency == currency))).scalars().all()
    year = date.today().year
    billed = [i for i in rows if i.status in ("issued", "debited", "credited") and i.issued_at and i.issued_at.year == year]
    unpaid = sum(float(i.amount) for i in rows if i.kind == "invoice" and i.status == "issued"
                 and i.debit_status == "insufficient_cash")
    held = sum(float(i.amount) for i in rows if i.status == "held")
    return {"currency": currency,
            "billed_this_year": round(sum(float(i.amount) for i in billed), 2) if billed else None,
            "unpaid": round(unpaid, 2), "held": round(held, 2), "at_risk": round(unpaid + held, 2) if rows else None}


async def compute(session: AsyncSession, firm_id: uuid.UUID, brains: list[dict] | None = None) -> dict:
    brains = brains if brains is not None else await gather_brains(session, firm_id)

    # ── Capacity & productivity ──
    total_runs = (await session.execute(select(func.count(AgentRun.id)).where(AgentRun.firm_id == firm_id))).scalar_one()
    decided = (
        await session.execute(
            select(func.count(Recommendation.id)).where(
                Recommendation.firm_id == firm_id,
                Recommendation.status.in_([RecommendationStatus.APPROVED, RecommendationStatus.MODIFIED,
                                           RecommendationStatus.DISMISSED, RecommendationStatus.EXECUTED]))
        )
    ).scalar_one()
    hours = round((total_runs + decided) * 25 / 60, 1)

    # ── Cost-to-serve, profitability, fee & margin (per household → firm) ──
    rows = []
    rev_total = cost_total = aum_total = 0.0
    margin_by_segment: dict[str, dict] = {}
    for b in brains:
        seg = b["household"]["segment"]
        aum = b["totals"]["total_value"]
        n_accounts = len(b["accounts"])
        has_alts = any(p["asset_class"] == "alternatives" for p in positions_of(b))
        revenue = aum * A.fee_rate(seg)
        cost = A.COST_BASE_PER_CLIENT + n_accounts * A.COST_PER_ACCOUNT + (A.COST_ALTERNATIVES_SURCHARGE if has_alts else 0)
        profit = revenue - cost
        margin = profit / revenue if revenue else 0.0
        rows.append({"household": b["household"]["name"], "segment": seg, "aum": round(aum, 2),
                     "revenue": round(revenue, 2), "cost_to_serve": round(cost, 2),
                     "profit": round(profit, 2), "margin": round(margin, 3)})
        rev_total += revenue
        cost_total += cost
        aum_total += aum
        seg_m = margin_by_segment.setdefault(seg, {"revenue": 0.0, "cost": 0.0, "aum": 0.0})
        seg_m["revenue"] += revenue
        seg_m["cost"] += cost
        seg_m["aum"] += aum

    billing = await _billing_position(session, firm_id)

    # ── Net New Assets (NNA) & ROA bps ──
    txns = (await session.execute(select(Transaction).where(Transaction.firm_id == firm_id))).scalars().all()
    _INFLOWS = {"buy", "capital_call"}
    _OUTFLOWS = {"sell", "distribution"}
    nna = (
        sum(float(t.amount or 0) for t in txns if t.txn_type in _INFLOWS)
        - sum(float(t.amount or 0) for t in txns if t.txn_type in _OUTFLOWS)
    )
    roa_bps = round((rev_total / aum_total) * 10_000, 1) if aum_total else 0.0

    # ── Growth, prospecting & referral ──
    pipeline = (
        await session.execute(
            select(func.count(OnboardingCase.id)).where(
                OnboardingCase.firm_id == firm_id,
                OnboardingCase.status.notin_([OnboardingStatus.APPROVED, OnboardingStatus.REJECTED]))
        )
    ).scalar_one()
    consolidation = 0.0
    for b in brains:
        seg = b["household"]["segment"]
        aum = b["totals"]["total_value"]
        held = sum((p.get("profile") or {}).get("held_away", 0) for p in b["persons"])
        if not held:
            share = A.wallet_share_default(seg)
            held = max(aum / share - aum, 0.0) if share else 0.0
        consolidation += held
    referral_ready = sum(1 for r in rows if r["margin"] > 0.4 and r["aum"] > 500_000)

    # ── NIGO rate ──
    total_cases = (
        await session.execute(select(func.count(OnboardingCase.id)).where(OnboardingCase.firm_id == firm_id))
    ).scalar_one()
    try:
        nigo_cases = (
            await session.execute(
                select(func.count(OnboardingCase.id)).where(
                    OnboardingCase.firm_id == firm_id,
                    OnboardingCase.nigo_flag.is_(True),
                )
            )
        ).scalar_one()
    except Exception:
        nigo_cases = 0
    nigo_rate = round(nigo_cases / total_cases, 3) if total_cases else 0.0

    # ── M&A / book-integration ──
    batches = (await session.execute(select(BookIntegrationBatch).where(BookIntegrationBatch.firm_id == firm_id))).scalars().all()
    committed = sum(1 for x in batches if x.status == "committed")
    total_conflicts = sum((x.stats or {}).get("conflicts", 0) for x in batches)

    return {
        "capacity": {"agent_runs": total_runs, "decisions": decided, "hours_reclaimed": hours,
                     "clients": len(brains), "firm_aum": round(aum_total, 2)},
        "cost_to_serve": {"revenue": round(rev_total, 2), "cost": round(cost_total, 2),
                          "profit": round(rev_total - cost_total, 2),
                          "firm_margin": round((rev_total - cost_total) / rev_total, 3) if rev_total else 0.0,
                          "by_household": sorted(rows, key=lambda r: -r["profit"])},
        # `fee_revenue` is still the segment-rate estimate (cost-to-serve margins are built on it) and
        # says so; what was actually billed sits beside it. The old "fee leakage" was a flat 3% of the
        # estimate - an invented number - and is replaced by what is genuinely unpaid or held.
        "fee_margin": {"fee_revenue": round(rev_total, 2), "fee_revenue_basis": "estimate",
                       "blended_fee_rate": round(rev_total / aum_total, 4) if aum_total else 0.0,
                       "billed_this_year": billing["billed_this_year"], "billing_currency": billing["currency"],
                       "fee_at_risk": billing["at_risk"], "unpaid": billing["unpaid"], "held": billing["held"],
                       "by_segment": {s: {"revenue": round(v["revenue"], 2),
                                          "margin": round((v["revenue"] - v["cost"]) / v["revenue"], 3) if v["revenue"] else 0.0,
                                          "effective_rate": round(v["revenue"] / v["aum"], 4) if v["aum"] else 0.0}
                                      for s, v in margin_by_segment.items()}},
        "growth": {"pipeline_cases": pipeline, "consolidation_opportunity": round(consolidation, 2),
                   "referral_ready_clients": referral_ready,
                   "net_new_assets": round(nna, 2), "roa_bps": round(roa_bps, 1),
                   "nigo_cases": nigo_cases, "nigo_rate": nigo_rate},
        "book_integration": {"acquisitions": len(batches), "committed": committed,
                             "open_conflicts": total_conflicts,
                             "reconciliation_progress": round(committed / len(batches), 2) if batches else 0.0},
    }
