"""The financial plan. Reading is for staff; an adviser or paraplanner records what the plan works from. The
agent-facing tools are the same actions behind a confirmation card. The view is of one household: a firm-wide list
would run a simulation per client, so with none it lists whom to open.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import financial_plan
from app.aurea_core.graph import list_households
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/plan", tags=["plan"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
WRITERS = (UserRole.ADVISER, UserRole.PARAPLANNER)


@router.get("")
async def read(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    if household is None:
        rows = await list_households(session, firm.id)
        return {"households": [{"household_id": h["id"], "household": h["name"]} for h in rows]}
    result = await financial_plan.for_household(session, household, firm_id=firm.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**result, "can_record": user.role in WRITERS}


@router.get("/goals")
async def goals_route(household: uuid.UUID, step_up: float | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                      session: AsyncSession = Depends(get_db)) -> dict:
    """Each goal sized and funded, with the vehicle categories for its horizon (B-394). It reads and records nothing; the
    step-up is a figure someone is trying out, and a goal is recorded or changed through the conversation."""
    from app.aurea_core import goal_sizing

    try:
        out = await goal_sizing.for_household(session, household, firm_id=firm.id, step_up_percent=step_up)
    except goal_sizing.SizingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if out is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**out, "can_record": user.role in WRITERS}


@router.get("/scenarios")
async def scenarios(household: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                    session: AsyncSession = Depends(get_db)) -> dict:
    """A household's saved plan scenarios, ranked by success points per 10,000 given up (B-179). Saving one is the
    conversation's, behind a Confirm card; this reads them."""
    from app.aurea_core import scenarios as sc

    try:
        return await sc.saved(session, firm, household)
    except sc.ScenarioError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

@router.get("/social-security")
async def social_security(household: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                          session: AsyncSession = Depends(get_db)) -> dict:
    """Social Security for a couple, laid out pair by pair (B-184). It reads and decides nothing."""
    from app.aurea_core import social_security as ss

    try:
        return await ss.couple(session, household, firm_id=firm.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.get("/triggers")
async def triggers(household: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                   session: AsyncSession = Depends(get_db)) -> dict:
    """What has given a plan a reason to be looked at again, and how fast it was (B-188). It reads and decides nothing."""
    from app.aurea_core import plan_record, plan_triggers

    try:
        plan_record.may_read(user.role)
    except plan_record.PlanRecordError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    out = await plan_triggers.for_household(session, household, firm_id=firm.id)
    if out is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return out


@router.get("/employer-benefits")
async def employer_benefits_route(household: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                  session: AsyncSession = Depends(get_db)) -> dict:
    """What an employer's plan gives each person, from the plan document (B-193). It reads and decides nothing."""
    from app.aurea_core import employer_benefits

    out = await employer_benefits.for_household(session, household, firm_id=firm.id)
    if out is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return out


@router.get("/guardrails")
async def guardrails_route(household: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                           session: AsyncSession = Depends(get_db)) -> dict:
    """Guardrail spending run beside fixed spending, from the terms on the household's IPS (B-180). It runs only when the IPS
    has terms, changes nothing, and the conversation runs it for terms a person says."""
    from app.aurea_core import guardrails

    on_file = await guardrails.terms_on_file(session, household, firm_id=firm.id)
    if on_file is None:
        return {"status": "not_assessed", "terms_from": None, "reason": "No guardrail terms are on the household's IPS."}
    out = await guardrails.for_household(session, household, firm_id=firm.id)
    if out is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return out
