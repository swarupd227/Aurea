"""Risk profiles: what each household's questionnaire indicates, beside what is on file.

Reading is for the people who advise on suitability or supervise it. Only an adviser decides, because
the decision is the suitability record. The agent-facing tool is the same action behind a confirmation
card. Clients answer through Canvas and do not see the result here: advice goes through their adviser.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import risk_profile
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/risk-profiles", tags=["risk-profiles"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
FORBIDDEN = "Only an adviser decides a client's risk profile."


class DecisionIn(BaseModel):
    decision: str
    reason: str | None = None


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await risk_profile.for_firm(db, firm.id), "can_decide": user.role == UserRole.ADVISER}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await risk_profile.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, "can_decide": user.role == UserRole.ADVISER}


@router.post("/mandates/{mandate_id}/decide")
async def decide(mandate_id: uuid.UUID, body: DecisionIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    if user.role != UserRole.ADVISER:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        return await risk_profile.decide(db, firm.id, mandate_id, decision=body.decision, reason=body.reason, by=user.email)
    except risk_profile.RiskProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
