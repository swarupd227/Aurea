"""IPS monitoring: each mandate read against the IPS its client accepted.

Reading is for the people who serve a client or supervise that service. Only an adviser records an IPS,
because it is a commitment to the client. A breach is accepted on the IPS monitor's proposal, under
decision rights, not here. The agent-facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import ips
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/ips", tags=["ips"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


class RecordIn(BaseModel):
    accepted_on: date | None = None
    document: str | None = None
    targets: dict[str, float] | None = None
    drift_band: float | None = None
    max_position: float | None = None
    min_cash: float | None = None
    restricted: list[str] | None = None
    reason: str | None = None


def _rights(user: User) -> dict:
    return {"can_record": user.role == UserRole.ADVISER}


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await ips.for_firm(db, firm.id), **_rights(user)}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await ips.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, **_rights(user)}


@router.get("/mandates/{mandate_id}")
async def read_mandate(mandate_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                       db: AsyncSession = Depends(get_db)):
    view = await ips.for_mandate(db, mandate_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Mandate not found.")
    return {**view, **_rights(user)}


@router.post("/mandates/{mandate_id}")
async def record(mandate_id: uuid.UUID, body: RecordIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    if user.role != UserRole.ADVISER:
        raise HTTPException(status_code=403, detail="Only an adviser records an IPS.")
    try:
        return await ips.record(db, mandate_id, firm_id=firm.id, by=user.email, role=str(user.role), **body.model_dump())
    except ips.IpsError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc))
