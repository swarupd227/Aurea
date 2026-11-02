"""Transfers into the firm with their files, and a case's rollover analysis. Reading is for staff; entries are recorded
through the conversation's tools, behind a Confirm card, from the document that says what happened.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import transfer_file
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/transfer-files", tags=["transfer-files"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


@router.get("")
async def read(case: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await transfer_file.for_case(session, firm.id, case_id=case)
    except transfer_file.TransferFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
