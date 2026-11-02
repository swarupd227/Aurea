"""Money coming in: bank links, deposits, holds, returns and standing instructions. Reading is for staff; each is
recorded through the conversation's tools, behind a Confirm card, from its document.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import money_in
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/money-in", tags=["money-in"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def read(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    return await money_in.for_firm(session, firm.id, household_id=household)
