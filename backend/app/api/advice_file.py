"""The advice file (B-175). Staff who advise or supervise read it; a client never does, because advice reaches a client
through their adviser. An adviser or paraplanner records through the conversation's tools, behind a Confirm card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import advice_file, advice_practice, counsel_review
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/advice-file", tags=["advice-file"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.BRANCH_LEADER))


@router.get("")
async def read(household: uuid.UUID | None = None, since: str | None = None, until: str | None = None, category: str | None = None,
               user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        result = (await advice_file.for_household(session, household, firm_id=firm.id) if household else
                  await advice_file.for_firm(session, firm.id, since=since, until=until, category=category))
    except advice_file.AdviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    result["can_record"] = str(user.role) in advice_file.RECORDERS
    return result


@router.get("/audit")
async def audit(user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    """India's adviser operating model and the sample audits (B-399). Compliance draws and records through the conversation."""
    result = await advice_practice.overview(session, firm)
    result["can_record"] = str(user.role) in advice_practice.RECORDERS
    return await counsel_review.annotate(session, firm, result)
