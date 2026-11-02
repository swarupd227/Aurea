"""The product shelf. Reading is for staff. The research team and the portfolio team decide on a product; an
adviser, paraplanner or operations records a client's standing; the research team or a branch leader turns on
"approved products only". The agent-facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import shelf
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/shelf", tags=["shelf"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.RESEARCH_CIO))
DECIDERS = (UserRole.RESEARCH_CIO, UserRole.PORTFOLIO_TEAM)
SWITCHERS = (UserRole.RESEARCH_CIO, UserRole.BRANCH_LEADER)


class DecisionIn(BaseModel):
    symbol: str
    status: str
    rationale: str = ""
    document: str = ""
    next_review_on: date | None = None
    minimum_class: str | None = None
    family: str | None = None
    share_class: str | None = None
    expense_ratio_pct: float | None = None
    diversified: bool | None = None


class EnforceIn(BaseModel):
    on: bool


@router.get("")
async def read_shelf(user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    return {**await shelf.for_firm(session, firm.id), "can_decide": user.role in DECIDERS, "can_switch": user.role in SWITCHERS}


@router.post("/decisions")
async def record_decision(body: DecisionIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    if user.role not in DECIDERS:
        raise HTTPException(status_code=403, detail="The research or portfolio team decides on a product.")
    try:
        await shelf.record_decision(session, firm.id, by=user.email, role=user.role, symbol=body.symbol, status=body.status, rationale=body.rationale,
                                    document=body.document, next_review_on=body.next_review_on, minimum_class=body.minimum_class, family=body.family,
                                    share_class=body.share_class, expense_ratio=body.expense_ratio_pct / 100 if body.expense_ratio_pct is not None else None,
                                    is_diversified=body.diversified)
    except shelf.ShelfError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await shelf.for_firm(session, firm.id)


@router.post("/enforcement")
async def set_enforcement(body: EnforceIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    if user.role not in SWITCHERS:
        raise HTTPException(status_code=403, detail="The research team or a branch leader sets this.")
    try:
        return await shelf.set_enforcement(session, firm.id, on=body.on, by=user.email)
    except shelf.ShelfError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
