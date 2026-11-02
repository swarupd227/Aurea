"""Aurea Core API — the unified client brain, planning & risk."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import behavioural, goal_tradeoff, planning, regulatory_countdown, retirement, tax_intelligence
from app.aurea_core.graph import household_brain, list_households
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.tenant import Firm

router = APIRouter(prefix="/api/core", tags=["core"], dependencies=[Depends(staff_user)])


@router.get("/households")
async def households(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await list_households(db, firm.id)


@router.get("/households/{household_id}")
async def household_detail(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    # Scoped to the caller's firm. The check that used to sit here compared the household's
    # id with the id that fetched it, so it could never fail; another firm's household
    # came back in full.
    brain = await household_brain(db, household_id, firm_id=firm.id)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")
    return brain


@router.get("/households/{household_id}/planning")
async def household_planning(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Goals-based projections, whole-portfolio risk and stress testing for a household."""
    brain = await household_brain(db, household_id, firm_id=firm.id)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")
    total = brain["totals"]["total_value"]
    by_class = brain["totals"]["by_asset_class"]
    allocation = {k: v for k, v in by_class.items()}

    risk = planning.portfolio_risk(allocation, total)
    stress = planning.stress_test(allocation)

    goal_projections = []
    for g in brain["goals"]:
        assumptions = g.get("assumptions") or {}
        proj = planning.project_goal(
            current_value=total * assumptions.get("funding_share", 1.0 / max(len(brain["goals"]), 1)),
            allocation=allocation,
            annual_contribution=assumptions.get("annual_contribution", 0),
            annual_withdrawal=assumptions.get("annual_withdrawal", 0),
            years=assumptions.get("years", 15),
            target_amount=g["target_amount"],
        )
        goal_projections.append({"goal": g["name"], "kind": g["kind"], **proj.__dict__})

    return {
        "household_id": str(household_id),
        "total_value": total,
        "allocation": allocation,
        "risk": risk,
        "stress_test": stress,
        "goals": goal_projections,
    }


@router.get("/households/{household_id}/retirement")
async def household_retirement(
    household_id: uuid.UUID,
    retirement_age: int | None = None, longevity_age: int | None = None,
    annual_income: float | None = None, annual_contribution: float | None = None,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Retirement income & decumulation projection. Optional query params drive what-if analysis."""
    overrides = {k: v for k, v in {
        "retirement_age": retirement_age, "longevity_age": longevity_age,
        "annual_income": annual_income, "annual_contribution": annual_contribution,
    }.items() if v is not None}
    plan = await retirement.for_household(db, household_id, firm_id=firm.id, overrides=overrides)
    if not plan:
        raise HTTPException(status_code=404, detail="Household not found")
    return plan


@router.get("/households/{household_id}/estate")
async def household_estate(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Estate & succession analysis — wealth breakdown, trust governance, heir readiness, succession gaps
    (`aurea_core/estate.py`, which the conversation's read_estate reads too)."""
    from app.aurea_core import estate

    result = await estate.for_household(db, household_id, firm_id=firm.id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.post("/households/{household_id}/goal-tradeoff")
async def household_goal_tradeoff(
    household_id: uuid.UUID,
    body: dict,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Multi-goal trade-off engine: shared-capital MC across all household goals.

    Body (all optional):
      priorities       {goal_id: rank}      — override goal priority ranks
      goal_overrides   {goal_id: {years_delta, target_scale, extra_contribution}}
      annual_income    float                — override for human-capital calculation
    """
    plan = await goal_tradeoff.for_household(
        db, household_id,
        firm_id=firm.id,
        priority_overrides=body.get("priorities"),
        goal_overrides=body.get("goal_overrides"),
        annual_income=body.get("annual_income"),
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Household not found")
    return plan


@router.get("/households/{household_id}/behavioural")
async def household_behavioural(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Behavioural finance profile: bias scores, stress playbook, transcript signals, adviser coaching."""
    result = await behavioural.for_household(db, household_id, firm_id=firm.id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/tax-intel")
async def household_tax_intel(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Tax intelligence: jurisdiction-dispatched modules (NZ / US / UK)."""
    result = await tax_intelligence.for_household(
        db, household_id, firm.jurisdiction or "NZ", firm_id=firm.id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/wash-sale-calendar")
async def household_wash_sale_calendar(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Household-wide wash-sale calendar (L200-4 §7.2) — the lots a loss-harvest would
    disallow right now, and which account's recent purchase is the reason. Lighter than
    the full tax-intel report (no jurisdiction modules), and household-scoped by
    construction (any account under any mandate under any person/entity in the household)."""
    result = await tax_intelligence.household_wash_sale_calendar(db, household_id, firm_id=firm.id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/performance")
async def household_performance(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Time-weighted and money-weighted return for a household's securities, with
    attribution and what the figure does and does not cover (`aurea_core/performance.py`)."""
    from app.aurea_core import performance

    result = await performance.household_performance(db, household_id, firm_id=firm.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/regulatory-countdown")
async def household_regulatory_countdown(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Jurisdiction-aware regulatory deadline analysis: US estate tax sunset, UK IHT pension inclusion."""
    result = await regulatory_countdown.for_household(
        db, household_id, firm.jurisdiction or "NZ", firm_id=firm.id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/regulatory-countdown/firm")
async def firm_regulatory_countdown(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Firm-wide regulatory countdown — all US and UK households with active countdown analyses."""
    results = await regulatory_countdown.for_firm(db, firm.id, firm.jurisdiction or "NZ")
    return {"results": results, "total": len(results)}


@router.post("/portfolio-whatif")
async def portfolio_whatif(
    body: dict, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Risk/return + stress for a hypothetical allocation (drives the client what-if panel)."""
    allocation = {k: float(v) for k, v in (body.get("allocation") or {}).items()}
    total = sum(allocation.values()) or 0.0
    return {
        "total": round(total, 2),
        "risk": planning.portfolio_risk(allocation, total),
        "stress": planning.stress_test(allocation),
    }


@router.post("/projection")
async def ad_hoc_projection(
    body: dict, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """What-if projection from an explicit allocation + assumptions (used by Studio/Canvas)."""
    proj = planning.project_goal(
        current_value=float(body.get("current_value", 0)),
        allocation=body.get("allocation", {"equity": 0.6, "fixed_income": 0.3, "cash": 0.1}),
        annual_contribution=float(body.get("annual_contribution", 0)),
        annual_withdrawal=float(body.get("annual_withdrawal", 0)),
        years=int(body.get("years", 15)),
        target_amount=float(body.get("target_amount", 0)),
    )
    return proj.__dict__
