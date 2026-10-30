"""Protection planning: the gaps, and the policies and incomes they are worked from.

Reading is for the people who advise on it or supervise it. Recording is for advisers, paraplanners
and operations, who hold the schedules. The agent-facing tools are the same actions behind a
confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import protection
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/protection", tags=["protection"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
FORBIDDEN = "Recording protection details is for advisers, paraplanners and operations."


class PolicyIn(BaseModel):
    person_id: uuid.UUID
    kind: str
    provider: str
    source_document: str
    sum_insured: float | None = None
    annual_benefit: float | None = None
    benefit_to_age: int | None = None
    annual_premium: float | None = None
    policy_number: str | None = None
    expires_on: date | None = None


class EndIn(BaseModel):
    reason: str


class IncomeIn(BaseModel):
    person_id: uuid.UUID
    annual_income: float
    source_document: str


def _guard(user: User) -> None:
    if user.role not in RECORDERS:
        raise HTTPException(status_code=403, detail=FORBIDDEN)


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await protection.for_firm(db, firm.id), "can_record": user.role in RECORDERS}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await protection.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, "can_record": user.role in RECORDERS}


@router.post("/policies")
async def record_policy(body: PolicyIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    _guard(user)
    try:
        return await protection.record_policy(db, firm.id, recorded_by=user.email, **body.model_dump())
    except protection.ProtectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/policies/{policy_id}/end")
async def end_policy(policy_id: uuid.UUID, body: EndIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                     db: AsyncSession = Depends(get_db)):
    _guard(user)
    try:
        return await protection.end_policy(db, firm.id, policy_id, reason=body.reason, by=user.email)
    except protection.ProtectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/income")
async def record_income(body: IncomeIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    _guard(user)
    try:
        return await protection.record_income(db, firm.id, person_id=body.person_id, annual_income=body.annual_income,
                                              source_document=body.source_document, recorded_by=user.email)
    except protection.ProtectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
