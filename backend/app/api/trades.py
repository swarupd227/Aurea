"""The trade blotter and tax documents. Reading is for staff; a document received is recorded through the
conversation's tool, behind a confirmation card.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import blotter, tax_documents
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api", tags=["trades"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("/trades")
async def trades(household: uuid.UUID | None = None, days: int = blotter.DEFAULT_DAYS, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    return await blotter.for_firm(session, firm.id, household_id=household, days=max(1, min(days, 365)))


@router.get("/tax-documents")
async def documents(household: uuid.UUID | None = None, year: int | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    result = await tax_documents.for_household(session, household, firm_id=firm.id, tax_year=year) if household else await tax_documents.for_firm(session, firm.id, tax_year=year)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return result
