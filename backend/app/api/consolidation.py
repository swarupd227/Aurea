"""The consolidation workbench, India (B-389): the page's reads.

Reading is for the people who serve a client or supervise that service; no client route reads it, because a
classification and an exit schedule are advice. The page records nothing itself: every change is the conversation's tool
behind its Confirm card, which checks the role (`aurea_core/consolidation.py`).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import consolidation, counsel_review
from app.core.db import get_db
from app.core.security import require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/consolidation", tags=["consolidation"])
ReaderDep = Depends(require_roles(*sorted(consolidation.READERS, key=lambda r: r.value)))


def _rights(user: User) -> dict:
    return {"can_classify": user.role in consolidation.CLASSIFIERS, "can_track": user.role in consolidation.TRACKERS,
            "can_record_facts": user.role in consolidation.FACT_RECORDERS}


@router.get("")
async def overview(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await consolidation.for_firm(db, firm.id), **_rights(user)}


@router.get("/households/{household_id}")
async def household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    view = await consolidation.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return {**await counsel_review.annotate(db, firm, view), **_rights(user)}
