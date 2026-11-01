"""Net worth: what a household owns, owes and spends beyond the managed book.

Reading is for the people who serve a client or supervise that service. An adviser, paraplanner or
operations records an item from a document, the same people who record protection details. The agent-facing
tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import balance_sheet
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/balance-sheet", tags=["balance-sheet"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
WRITERS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS)


class ItemIn(BaseModel):
    kind: str
    name: str
    amount: float | None = None
    as_of: date | None = None
    source_document: str = ""
    person_id: uuid.UUID | None = None
    annual_payment: float | None = None
    interest_rate: float | None = None
    ends_on: date | None = None
    currency: str | None = None
    replaces_id: uuid.UUID | None = None


class EndIn(BaseModel):
    reason: str | None = None


def _rights(user: User) -> dict:
    return {"can_record": user.role in WRITERS}


def _writer(user: User) -> None:
    if user.role not in WRITERS:
        raise HTTPException(status_code=403, detail="Only an adviser, paraplanner or operations records these.")


def _refused(exc: balance_sheet.BalanceSheetError) -> HTTPException:
    return HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc))


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await balance_sheet.for_firm(db, firm.id), **_rights(user)}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await balance_sheet.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, **_rights(user)}


@router.post("/households/{household_id}")
async def record(household_id: uuid.UUID, body: ItemIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await balance_sheet.record_item(db, household_id, firm_id=firm.id, by=user.email, **body.model_dump())
    except balance_sheet.BalanceSheetError as exc:
        raise _refused(exc)


@router.post("/items/{item_id}/end")
async def end(item_id: uuid.UUID, body: EndIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
              db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await balance_sheet.end_item(db, item_id, firm_id=firm.id, reason=body.reason, by=user.email)
    except balance_sheet.BalanceSheetError as exc:
        raise _refused(exc)
