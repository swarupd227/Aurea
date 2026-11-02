"""The document matrix and one case's check before submission. Reading is for staff; detail is recorded through the
conversation's tool, behind a Confirm card, from the paperwork.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import document_matrix
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

router = APIRouter(prefix="/api/document-matrix", tags=["document-matrix"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def read(case: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    result = document_matrix.matrix(firm)
    if case is not None:
        row = await session.get(OnboardingCase, case)
        if row is None or row.firm_id != firm.id:
            raise HTTPException(status_code=404, detail="Case not found.")
        result["check"] = {**await document_matrix.check_case(session, row, firm), "case_id": str(row.id), "case": row.prospect_name}
    return result
