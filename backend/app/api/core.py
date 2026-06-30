"""Aurea Core API — the unified client brain, planning & risk."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import planning, retirement
from app.aurea_core.graph import household_brain, list_households
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.graph import Goal
from app.models.tenant import Firm

router = APIRouter(prefix="/api/core", tags=["core"], dependencies=[Depends(staff_user)])


@router.get("/households")
async def households(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await list_households(db, firm.id)


@router.get("/households/{household_id}")
async def household_detail(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    brain = await household_brain(db, household_id)
    if not brain or brain["household"]["id"] != str(household_id):
        raise HTTPException(status_code=404, detail="Household not found")
    return brain


@router.get("/households/{household_id}/planning")
async def household_planning(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Goals-based projections, whole-portfolio risk and stress testing for a household."""
    brain = await household_brain(db, household_id)
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
    plan = await retirement.for_household(db, household_id, overrides=overrides)
    if not plan:
        raise HTTPException(status_code=404, detail="Household not found")
    return plan


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
