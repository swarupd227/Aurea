"""The KYC registry status, India (B-386): the page's read.

Staff who serve or supervise a client read it; a client reads only their own plain-words status on their page
(`GET /api/canvas/kyc`). The page records nothing itself: every change is the conversation's tool behind its Confirm card,
which checks the role (`aurea_core/kyc_registry.py`).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import identity_checks, kyc_registry
from app.core.db import get_db
from app.core.security import require_roles
from app.core.tools import TOOLS
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/kyc-registry", tags=["kyc-registry"])
# The roles the conversation's read holds, so the page and the tool cannot disagree.
ReaderDep = Depends(require_roles(*sorted(TOOLS["read_kyc_registry"].roles_required, key=lambda r: r.value)))


@router.get("")
async def book(household_id: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               db: AsyncSession = Depends(get_db)):
    try:
        view = await kyc_registry.book(db, firm, household_id=household_id)
    except kyc_registry.RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {**view, "can_record": user.role in kyc_registry.RECORDERS}


@router.get("/checks")
async def checks(household_id: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    """Identity and bank checks: the human queue, what can be retried and what passed (B-387)."""
    try:
        view = await identity_checks.overview(db, firm, household_id=household_id)
    except identity_checks.CheckError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {**view, "can_run": user.role in identity_checks.RUNNERS, "can_decide": user.role in identity_checks.DECIDERS}
