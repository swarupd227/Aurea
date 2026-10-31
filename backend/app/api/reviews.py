"""Client reviews: when each household was last reviewed, and when the next is due.

Reading is for the people who serve a client or supervise that service. An adviser or paraplanner may
record a review held or book one; only an adviser changes a cadence, because it is a commitment to the
client. The agent-facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import reviews
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
WRITERS = (UserRole.ADVISER, UserRole.PARAPLANNER)


class RecordIn(BaseModel):
    held_on: date | None = None
    covered: list[str] = []
    notes: str | None = None
    meeting_id: uuid.UUID | None = None


class CadenceIn(BaseModel):
    months: int
    reason: str | None = None


class BookIn(BaseModel):
    date: date
    time: str | None = None


def _rights(user: User) -> dict:
    return {"can_record": user.role in WRITERS, "can_set_cadence": user.role == UserRole.ADVISER}


def _writer(user: User) -> None:
    if user.role not in WRITERS:
        raise HTTPException(status_code=403, detail="Only an adviser or paraplanner records or books a review.")


def _refused(exc: reviews.ReviewError) -> HTTPException:
    return HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc))


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await reviews.for_firm(db, firm.id), **_rights(user)}


@router.get("/households/{household_id}")
async def read_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    view = await reviews.for_household(db, household_id, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**view, **_rights(user)}


@router.post("/households/{household_id}")
async def record(household_id: uuid.UUID, body: RecordIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                 db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await reviews.record(db, household_id, firm_id=firm.id, held_on=body.held_on, covered=body.covered,
                                    notes=body.notes, meeting_id=body.meeting_id, by=user.email, role=str(user.role))
    except reviews.ReviewError as exc:
        raise _refused(exc)


@router.post("/households/{household_id}/cadence")
async def set_cadence(household_id: uuid.UUID, body: CadenceIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    if user.role != UserRole.ADVISER:
        raise HTTPException(status_code=403, detail="Only an adviser changes a client's review cadence.")
    try:
        return await reviews.set_cadence(db, household_id, firm_id=firm.id, months=body.months, reason=body.reason, by=user.email)
    except reviews.ReviewError as exc:
        raise _refused(exc)


@router.post("/households/{household_id}/book")
async def book(household_id: uuid.UUID, body: BookIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
               db: AsyncSession = Depends(get_db)):
    _writer(user)
    try:
        return await reviews.book(db, household_id, firm_id=firm.id, on=body.date, at=body.time, by=user.email)
    except reviews.ReviewError as exc:
        raise _refused(exc)
