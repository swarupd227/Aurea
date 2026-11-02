"""The consent registry. Reading is for staff; a consent is recorded through the conversation's tool, behind a
Confirm card, from its evidence.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import consents
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/consents", tags=["consents"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def registry(household: uuid.UUID | None = None, case: uuid.UUID | None = None, user: User = ReaderDep,
                   firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await consents.registry(session, firm.id, household_id=household, case_id=case)
    except consents.ConsentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
