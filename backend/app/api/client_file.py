"""The client file. Reading is for staff. An adviser, paraplanner or operations records from a document, through the
conversation's tools, which are behind a confirmation card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import client_file
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/client-file", tags=["client-file"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def read(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    result = await client_file.for_household(session, household, firm_id=firm.id) if household else await client_file.for_firm(session, firm.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return result
