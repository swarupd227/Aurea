"""Screening decisions and EDD sign-off. Reading is for the people who run or supervise onboarding; deciding is done
through the conversation's tools, behind a Confirm card, by compliance or operations.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import aml_review
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/aml", tags=["aml"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def reviews(case: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await aml_review.for_case(session, case, firm_id=firm.id) if case else await aml_review.for_firm(session, firm.id)
    except aml_review.AmlReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
