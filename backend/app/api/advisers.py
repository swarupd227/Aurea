"""Whose client each household is, and the day at the top of the cockpit. Assigning goes through the conversation's
tool, behind a Confirm card, with its reason.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import advisers
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/advisers", tags=["advisers"])
STAFF = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.COMPLIANCE,
         UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN)


@router.get("")
async def read(user: User = Depends(require_roles(*STAFF)), firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    return await advisers.for_firm(session, firm.id)


@router.get("/my-day")
async def my_day(user: User = Depends(require_roles(*STAFF)), firm: Firm = Depends(current_firm),
                 session: AsyncSession = Depends(get_db)) -> dict:
    return await advisers.day(session, firm, user)
