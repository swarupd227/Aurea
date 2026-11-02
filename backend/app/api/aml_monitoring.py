"""Transaction monitoring alerts, investigations and report clocks. Compliance and operations only: none of it reaches a
client or an adviser. Decisions are taken through the conversation's tools, behind a Confirm card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import txn_monitoring
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/aml-monitoring", tags=["aml-monitoring"])
ReaderDep = Depends(require_roles(UserRole.COMPLIANCE, UserRole.OPERATIONS))


@router.get("")
async def read(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    return await txn_monitoring.for_firm(session, firm.id, household_id=household)
