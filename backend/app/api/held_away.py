"""Held away, line by line, and how it is doing (B-198).

Reading is for the people who serve a client or supervise that service, as net worth is; no client route reads it,
because what it finds is advice and goes through the adviser. An adviser, paraplanner or operations records a holding
from a statement or ends one. The conversation's tools are the same actions behind a Confirm card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import held_away
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/held-away", tags=["held-away"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
WRITERS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS)


class HoldingIn(BaseModel):
    institution: str = ""
    instrument_name: str = ""
    value: float | None = None
    value_on: date | None = None
    source_document: str = ""
    currency: str | None = None
    person_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    account_label: str | None = None
    symbol: str | None = None
    isin: str | None = None
    asset_class: str | None = None
    quantity: float | None = None
    replaces_id: uuid.UUID | None = None


class EndIn(BaseModel):
    reason: str | None = None


def _rights(user: User) -> dict:
    return {"can_record": user.role in WRITERS}


def _writer(user: User) -> None:
    if user.role not in WRITERS:
        raise HTTPException(status_code=403, detail="Only an adviser, paraplanner or operations records these.")


def _refused(exc: held_away.HeldAwayError) -> HTTPException:
    return HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc))


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await held_away.for_firm(db, firm.id), **_rights(user)}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await held_away.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, **_rights(user)}


@router.post("/households/{household_id}")
async def record(household_id: uuid.UUID, body: HoldingIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await held_away.record_holding(db, household_id, firm_id=firm.id, by=user.email, **body.model_dump())
    except held_away.HeldAwayError as exc:
        raise _refused(exc)


@router.post("/holdings/{holding_id}/end")
async def end(holding_id: uuid.UUID, body: EndIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
              db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await held_away.end_holding(db, holding_id, firm_id=firm.id, reason=body.reason, by=user.email)
    except held_away.HeldAwayError as exc:
        raise _refused(exc)
