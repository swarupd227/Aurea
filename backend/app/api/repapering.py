"""Bulk repapering campaigns, for staff. Campaigns are started and answers recorded through the conversation's tools,
behind a Confirm card, from their evidence.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import repapering
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/repapering", tags=["repapering"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def read(campaign: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    return await repapering.for_firm(session, firm.id, campaign_id=campaign)
