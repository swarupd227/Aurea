"""Reconciling the book against custodian statements.

Reading is open to staff. Importing a statement and acting on a break are limited to operations
and the portfolio team - they are the ones who can take a difference to a custodian. The agent
facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import reconciliation
from app.core.db import get_db
from app.core.decision_rights import STAFF
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])
StaffDep = Depends(require_roles(*STAFF))
ACTORS = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})
FORBIDDEN = ("Importing a custodian statement and acting on a break are for operations and the portfolio "
             "team, who can take a difference to the custodian.")


class StatementIn(BaseModel):
    account_id: uuid.UUID
    as_of: date
    positions: list[dict]
    cash_balance: float | None = None
    currency: str | None = None
    custodian: str | None = None
    source_document: str


class ResolveIn(BaseModel):
    action: str
    note: str | None = None


@router.get("")
async def read(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await reconciliation.overview(db, firm.id), "can_act": user.role in ACTORS}


@router.post("/statements")
async def import_statement(body: StatementIn, user: User = StaffDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    if user.role not in ACTORS:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        return await reconciliation.import_statement(
            db, firm.id, account_id=body.account_id, as_of=body.as_of, positions=body.positions,
            cash_balance=body.cash_balance, currency=body.currency, custodian=body.custodian,
            source_document=body.source_document, imported_by=user.email)
    except reconciliation.ReconciliationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/breaks/{break_id}/resolve")
async def resolve(break_id: uuid.UUID, body: ResolveIn, user: User = StaffDep,
                  firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    if user.role not in ACTORS:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        return await reconciliation.resolve_break(db, firm.id, break_id, action=body.action, note=body.note,
                                                  by=user.email)
    except reconciliation.ReconciliationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
