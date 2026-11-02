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
