"""The plan of record (B-181). Staff who advise, review or supervise read it; a client and operations do not. A
baseline, a review and a delivery are recorded through the conversation's tools, behind a Confirm card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import plan_record
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/plan-record", tags=["plan-record"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.BRANCH_LEADER))


@router.get("")
async def read(household: uuid.UUID | None = None, since: str | None = None, until: str | None = None,
               user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        result = (await plan_record.for_household(session, household, firm_id=firm.id) if household else
                  await plan_record.for_firm(session, firm.id, role=str(user.role), user_id=user.id, since=since, until=until))
    except plan_record.PlanRecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    result["can_prepare"] = str(user.role) in plan_record.PREPARERS
    result["can_review"] = str(user.role) in plan_record.REVIEWERS
    return result
