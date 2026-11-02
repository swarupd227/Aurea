"""Equity compensation and concentrated stock. Reading is for staff; an adviser or paraplanner records a grant
or a plan. The agent-facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import equity_comp
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/equity-comp", tags=["equity-comp"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
WRITERS = (UserRole.ADVISER, UserRole.PARAPLANNER)


class GrantIn(BaseModel):
    person_id: uuid.UUID
    kind: str
    employer: str = ""
    units: float | None = None
    grant_date: date | None = None
    source_document: str = ""
    symbol: str | None = None
    strike: float | None = None
    vesting: list[dict] | None = None
    expires_on: date | None = None


class PlanIn(BaseModel):
    household_id: uuid.UUID
    symbol: str
    method: str
    target_weight_pct: float
    target_date: date | None = None
    rationale: str = ""
    source_document: str = ""


class EndIn(BaseModel):
    reason: str = ""


def _writer(user: User) -> None:
    if user.role not in WRITERS:
        raise HTTPException(status_code=403, detail="An adviser or paraplanner records this.")


@router.get("")
async def read(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    result = await equity_comp.for_household(session, household, firm_id=firm.id) if household else await equity_comp.for_firm(session, firm.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**result, "can_record": user.role in WRITERS}


@router.post("/grants")
async def record_grant(body: GrantIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    _writer(user)
    try:
        row = await equity_comp.record_grant(session, body.person_id, firm_id=firm.id, by=user.email, kind=body.kind, employer=body.employer, units=body.units,
                                             grant_date=body.grant_date, source_document=body.source_document, symbol=body.symbol, strike=body.strike,
                                             vesting=body.vesting, expires_on=body.expires_on)
    except equity_comp.EquityCompError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await equity_comp.for_household(session, row.household_id, firm_id=firm.id)


@router.post("/grants/{grant_id}/end")
async def end_grant(grant_id: uuid.UUID, body: EndIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    _writer(user)
    try:
        row = await equity_comp.end_grant(session, grant_id, firm_id=firm.id, by=user.email, reason=body.reason)
    except equity_comp.EquityCompError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await equity_comp.for_household(session, row.household_id, firm_id=firm.id)


@router.post("/plans")
async def record_plan(body: PlanIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    _writer(user)
    try:
        await equity_comp.record_plan(session, body.household_id, firm_id=firm.id, by=user.email, symbol=body.symbol, method=body.method,
                                      target_weight=body.target_weight_pct / 100, target_date=body.target_date, rationale=body.rationale, source_document=body.source_document)
    except equity_comp.EquityCompError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await equity_comp.for_household(session, body.household_id, firm_id=firm.id)
