"""India's asset register, will checks and NRI planning (B-398), read by staff. A client reads none of it. Every record is
made through the conversation's tools, behind a Confirm card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import counsel_review, estate_register, nri_planning
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/india-planning", tags=["india-planning"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("/estate")
async def estate(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 session: AsyncSession = Depends(get_db)) -> dict:
    try:
        result = (await estate_register.household_register(session, firm, household) if household
                  else await estate_register.overview(session, firm))
    except estate_register.RegisterError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result["can_record"] = user.role in estate_register.RECORDERS
    return await counsel_review.annotate(session, firm, result)


@router.get("/nri")
async def nri(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
              session: AsyncSession = Depends(get_db)) -> dict:
    try:
        result = (await nri_planning.household_view(session, firm, household) if household
                  else await nri_planning.overview(session, firm))
    except nri_planning.PlanningError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result["can_record"] = user.role in nri_planning.RECORDERS
    return await counsel_review.annotate(session, firm, result)
