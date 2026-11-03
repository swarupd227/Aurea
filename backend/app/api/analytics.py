"""Analytics API — the five analytics layers over the Unified Client Brain (Companion §2)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core.analytics import (
    adoption, advice, client_intelligence, overview, portfolio, practice, risk_data,
)
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(staff_user)])


@router.get("/overview")
async def get_overview(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Everything in one governed read: headline KPIs, the maturity climb, and all five layers."""
    return await overview.compute_all(db, firm.id)


@router.get("/client-intelligence")
async def get_client_intelligence(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await client_intelligence.compute(db, firm.id)


@router.get("/portfolio")
async def get_portfolio(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await portfolio.compute(db, firm.id)


@router.get("/advice")
async def get_advice(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await advice.compute(db, firm.id)


@router.get("/practice")
async def get_practice(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await practice.compute(db, firm.id)


@router.get("/risk-data")
async def get_risk_data(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await risk_data.compute(db, firm.id)


@router.get("/adoption")
async def get_adoption(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Layer 2.6 — Agent adoption, feature uptake, per-adviser engagement, and ROI metrics."""
    return await adoption.compute(db, firm.id)


@router.get("/onboarding")
async def get_onboarding_metrics(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """F5 — Operating metrics dashboard for the onboarding pipeline."""
    from sqlalchemy import case as sa_case, cast, Float, Integer, literal_column
    from app.models.onboarding import OnboardingCase
    from app.core.db import utcnow
    from datetime import timedelta
    import statistics

    cases = (await db.execute(
        select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
    )).scalars().all()

    total = len(cases)
    now = utcnow()

    # Funnel by status
    funnel: dict[str, int] = {}
    for c in cases:
        funnel[c.status] = funnel.get(c.status, 0) + 1

    # Cycle time and first-pass yield come from the onboarding metrics, so the two dashboards cannot disagree.
    # This one used to measure to the case's last edit, and counted open cases in the yield.
    from app.aurea_core import onboarding_metrics

    m = onboarding_metrics.compute(list(cases), parties_by_case={}, disclosures_by_case={}, transfers_by_case={}, now=now)
    ct = m["cycle_time_days"]
    cycle_times = [
        {"registration_type": rt, "count": s["n"], "p50_days": s["p50"] or 0.0, "p90_days": s["p90"] or 0.0}
        for rt, s in ct["by_registration_type"].items()
    ]
    overall_p50 = ct["intake_to_activation"]["p50"] or 0.0
    overall_p90 = ct["intake_to_activation"]["p90"] or 0.0

    nigo_count = sum(1 for c in cases if getattr(c, "nigo_flag", False))
    fpy = m["quality"]["first_pass_yield_pct"]
    first_pass_yield = round(fpy / 100, 3) if fpy is not None else 0.0

    # NIGO root-cause breakdown
    nigo_causes: dict[str, int] = {}
    for c in cases:
        rc = getattr(c, "nigo_root_cause", None)
        if rc:
            nigo_causes[rc] = nigo_causes.get(rc, 0) + 1

    # Abandonment: cases stalled > 3 days (not terminal)
    terminal = {"approved", "rejected"}
    amber_threshold = now - timedelta(days=3)
    red_threshold = now - timedelta(days=7)
    # Postgres keeps the zone and SQLite drops it; a time with none is UTC.
    from datetime import timezone

    touched = lambda c: c.updated_at.replace(tzinfo=timezone.utc) if c.updated_at.tzinfo is None else c.updated_at  # noqa: E731
    stalled_amber = [c for c in cases if c.status not in terminal and c.updated_at and red_threshold <= touched(c) < amber_threshold]
    stalled_red = [c for c in cases if c.status not in terminal and c.updated_at and touched(c) < red_threshold]
    abandonment_by_status: dict[str, int] = {}
    for c in stalled_red + stalled_amber:
        abandonment_by_status[c.status] = abandonment_by_status.get(c.status, 0) + 1

    # EDD / AML backlog
    edd_pending = [c for c in cases if getattr(c, "edd_status", None) == "edd_pending"]
    aml_by_tier: dict[str, int] = {}
    for c in cases:
        tier = getattr(c, "aml_risk_tier", None)
        if tier:
            aml_by_tier[tier] = aml_by_tier.get(tier, 0) + 1

    # Avg readiness score
    scored = [c for c in cases if getattr(c, "readiness_score", None) is not None]
    avg_readiness = round(sum(c.readiness_score for c in scored) / len(scored), 1) if scored else None

    return {
        "total_cases": total,
        "funnel": funnel,
        "cycle_time": {
            "overall_p50_days": overall_p50,
            "overall_p90_days": overall_p90,
            "by_registration_type": cycle_times,
        },
        "first_pass_yield": first_pass_yield,
        "nigo": {
            "total": nigo_count,
            "rate": round(nigo_count / total, 3) if total else 0.0,
            "by_root_cause": nigo_causes,
        },
        "abandonment": {
            "stalled_amber_3d": len(stalled_amber),
            "stalled_red_7d": len(stalled_red),
            "by_status": abandonment_by_status,
        },
        "edd_backlog": {
            "pending_count": len(edd_pending),
        },
        "aml_by_tier": aml_by_tier,
        "avg_readiness_score": avg_readiness,
    }


@router.get("/pte-tracker")
async def get_pte_tracker(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """U7 — PTE 2020-02 status tracker: all rollover cases with rationale memo status."""
    from app.models.onboarding import OnboardingCase
    _ROLLOVER_TYPES = {"employer_rollover", "traditional_ira", "roth_ira"}
    cases = (await db.execute(
        select(OnboardingCase).where(
            OnboardingCase.firm_id == firm.id,
            OnboardingCase.registration_type.in_(list(_ROLLOVER_TYPES)),
        ).order_by(OnboardingCase.created_at.desc())
    )).scalars().all()

    rows = []
    for c in cases:
        proposal = c.proposal or {}
        rows.append({
            "case_id": str(c.id),
            "prospect_name": c.prospect_name,
            "registration_type": getattr(c, "registration_type", None),
            "status": c.status,
            "pte_status": getattr(c, "pte_status", None),
            "has_memo": bool(proposal.get("pte_2020_02_memo")),
            "has_fee_comparison": bool(proposal.get("pte_fee_comparison")),
            "cip_status": getattr(c, "cip_status", None),
            "aml_risk_tier": getattr(c, "aml_risk_tier", None),
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })

    total = len(rows)
    memo_generated = sum(1 for r in rows if r["has_memo"])
    missing_memo = total - memo_generated
    by_pte_status: dict[str, int] = {}
    for r in rows:
        k = r["pte_status"] or "pending"
        by_pte_status[k] = by_pte_status.get(k, 0) + 1

    return {
        "total_rollover_cases": total,
        "memo_generated": memo_generated,
        "missing_memo": missing_memo,
        "by_pte_status": by_pte_status,
        "cases": rows,
    }


@router.get("/maturity")
async def get_maturity():
    return {"maturity": overview.MATURITY, "layers": overview.LAYER_META}


@router.get("/tax-book")
async def get_tax_book(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Book-level tax intelligence: jurisdiction-dispatched, aggregated across all households."""
    from app.aurea_core import tax_intelligence
    return await tax_intelligence.for_firm(db, firm.id, firm.jurisdiction or "NZ")


@router.get("/scorecards")
async def get_scorecards(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Per-adviser scorecard: AUM, household count, recommendation throughput, meetings held."""
    from app.models.graph import Household
    from app.models.governance import Recommendation
    from app.models.engagement import Meeting
    from app.models.enums import RecommendationStatus

    # All adviser users for this firm.
    advisers = (await db.execute(
        select(User).where(User.firm_id == firm.id, User.role == "adviser", User.is_active.is_(True))
    )).scalars().all()

    from app.aurea_core.advisers import of_households
    from app.models.graph import LegalEntity, Person
    held = await of_households(db, firm.id)

    scorecards = []
    for adv in advisers:
        # The adviser's households (aurea_core/advisers.py), and the people and entities in them.
        household_ids = [hid for hid, uid in held.items() if uid == adv.id]
        person_ids = list((await db.execute(select(Person.id).where(Person.household_id.in_(household_ids)))).scalars().all()) if household_ids else []
        entity_ids = list((await db.execute(select(LegalEntity.id).where(LegalEntity.household_id.in_(household_ids)))).scalars().all()) if household_ids else []
        hh_count = len(household_ids)

        # AUM: sum of holding market values via Account → Mandate → person/entity.
        from sqlalchemy import or_ as _or
        from app.models.graph import Account, Mandate
        from app.models.portfolio import Holding
        aum = 0.0
        if person_ids or entity_ids:
            clauses = []
            if person_ids:
                clauses.append(Mandate.person_id.in_(person_ids))
            if entity_ids:
                clauses.append(Mandate.entity_id.in_(entity_ids))
            aum_row = (await db.execute(
                select(func.coalesce(func.sum(Holding.market_value), 0))
                .join(Account, Account.id == Holding.account_id)
                .join(Mandate, Mandate.id == Account.mandate_id)
                .where(_or(*clauses))
            )).scalar_one()
            aum = float(aum_row)

        # Recommendations approved in last 90 days.
        from datetime import timedelta
        from app.core.db import utcnow
        since = utcnow() - timedelta(days=90)
        approved_count = (await db.execute(
            select(func.count(Recommendation.id)).where(
                Recommendation.firm_id == firm.id,
                Recommendation.status == RecommendationStatus.APPROVED,
                Recommendation.updated_at >= since,
            )
        )).scalar_one()

        # Meetings in last 90 days for these households.
        meeting_count = 0
        if household_ids:
            meeting_count = (await db.execute(
                select(func.count(Meeting.id)).where(
                    Meeting.firm_id == firm.id,
                    Meeting.household_id.in_(household_ids),
                    Meeting.scheduled_at >= since,
                )
            )).scalar_one()

        scorecards.append({
            "adviser_id": str(adv.id),
            "adviser_name": adv.full_name,
            "adviser_email": adv.email,
            "household_count": hh_count,
            "aum": aum,
            "recs_approved_90d": approved_count,
            "meetings_90d": meeting_count,
        })

    scorecards.sort(key=lambda x: x["aum"], reverse=True)
    return {"scorecards": scorecards}
