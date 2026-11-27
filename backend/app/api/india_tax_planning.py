"""India's tax planning, read beside the conversation (B-396): the Tax page's after-tax comparison and its reviews. Reads
only: each change is a tool behind its Confirm card."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import india_after_tax, india_tax_year
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.tenant import Firm

router = APIRouter(prefix="/api/india-tax", tags=["india-tax"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN))


@router.get("/after-tax", dependencies=[ReaderDep])
async def after_tax(household_id: uuid.UUID | None = None, amount: float | None = None, months: int | None = None,
                    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        return await india_after_tax.comparison(db, firm, household_id=household_id, amount=amount, months=months)
    except india_after_tax.AfterTaxError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/reviews", dependencies=[ReaderDep])
async def reviews(household_id: uuid.UUID | None = None, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        return await india_tax_year.overview(db, firm, household_id=household_id)
    except india_tax_year.TaxYearError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
