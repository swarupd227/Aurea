"""India's agreement file, the fund-house acceptance list and a trust case's beneficial owners (B-391), read by staff.
A client reads none of it. Every record is made through the conversation's tools, behind a Confirm card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import counsel_review, fund_acceptance, india_agreements
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/india-agreements", tags=["india-agreements"])
ReaderDep = Depends(require_roles(*india_agreements.READERS))
ListDep = Depends(require_roles(*india_agreements.READERS, UserRole.RESEARCH_CIO))


@router.get("")
async def agreement_file(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         session: AsyncSession = Depends(get_db)) -> dict:
    try:
        result = (await india_agreements.household_view(session, firm, household) if household
                  else await india_agreements.overview(session, firm))
    except india_agreements.AgreementError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result["can_record"] = user.role in india_agreements.RECORDERS
    return await counsel_review.annotate(session, firm, result)


@router.get("/acceptance")
async def acceptance(user: User = ListDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    result = await fund_acceptance.overview(session, firm)
    result["can_record"] = user.role in fund_acceptance.RECORDERS
    return await counsel_review.annotate(session, firm, result)
