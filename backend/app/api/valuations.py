"""Declared valuations for private holdings (L100 §9).

Reading is open to staff. Recording changes client balances and the fees charged on them, so
it is limited to operations and the portfolio team - and require_roles' blanket administrator
pass is deliberately not honoured, as for the other things that move money.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import valuations
from app.core.db import get_db
from app.core.decision_rights import STAFF
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/valuations", tags=["valuations"])
StaffDep = Depends(require_roles(*STAFF))
RECORDERS = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})


@router.get("/private")
async def get_private(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {"instruments": await valuations.private_valuations(db, firm.id), "can_record": user.role in RECORDERS}


class ValuationIn(BaseModel):
    instrument_id: uuid.UUID
    unit_value: float
    as_of: date
    source_document: str
    notes: str | None = None


@router.post("")
async def record(body: ValuationIn, user: User = StaffDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    if user.role not in RECORDERS:
        raise HTTPException(status_code=403, detail="Recording a valuation changes client balances and fees, "
                            "so only operations or the portfolio team may do it.")
    try:
        return await valuations.record_valuation(
            db, firm.id, instrument_id=body.instrument_id, as_of=body.as_of, unit_value=body.unit_value,
            source_document=body.source_document, notes=body.notes, recorded_by=user.email)
    except valuations.ValuationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
