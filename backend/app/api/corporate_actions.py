"""Corporate actions API (L200-4 §5) — capture, entitlement, election, posting."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import corporate_actions as engine
from app.aurea_core.corporate_actions import CorporateActionError
from app.core.decision_rights import STAFF
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.corporate_actions import (
    ACTION_STATUSES, ACTION_TYPES, CorporateAction, CorporateActionEntitlement,
)
from app.models.identity import User
from app.models.portfolio import Instrument
from app.models.tenant import Firm

router = APIRouter(prefix="/api/corporate-actions", tags=["corporate-actions"])
# Capture/posting is asset-servicing ops work (L200-4 §10's "settlements/asset servicing"
# team); electing on a client's behalf and reading stay open to any staff role.
StaffDep = Depends(require_roles(*STAFF))
OpsDep = Depends(require_roles(UserRole.OPERATIONS, UserRole.ADMIN, UserRole.COMPLIANCE))


def _action_out(a: CorporateAction) -> dict:
    return {
        "id": str(a.id), "instrument_id": str(a.instrument_id), "action_type": a.action_type,
        "status": a.status, "is_voluntary": a.is_voluntary,
        "record_date": a.record_date.isoformat() if a.record_date else None,
        "ex_date": a.ex_date.isoformat() if a.ex_date else None,
        "payable_date": a.payable_date.isoformat() if a.payable_date else None,
        "election_deadline": a.election_deadline.isoformat() if a.election_deadline else None,
        "details": a.details, "source": a.source,
    }


def _entitlement_out(e: CorporateActionEntitlement) -> dict:
    return {
        "id": str(e.id), "corporate_action_id": str(e.corporate_action_id),
        "account_id": str(e.account_id), "status": e.status,
        "shares_entitled": float(e.shares_entitled),
        "election_choice": e.election_choice,
        "election_made_at": e.election_made_at.isoformat() if e.election_made_at else None,
        "cash_amount": float(e.cash_amount) if e.cash_amount is not None else None,
        "share_amount": float(e.share_amount) if e.share_amount is not None else None,
        "posted_at": e.posted_at.isoformat() if e.posted_at else None,
        "verified_at": e.verified_at.isoformat() if e.verified_at else None,
        "cost_basis_adjustment": e.cost_basis_adjustment,
    }


class ActionIn(BaseModel):
    instrument_id: uuid.UUID
    action_type: str
    is_voluntary: bool = False
    record_date: date | None = None
    ex_date: date | None = None
    payable_date: date | None = None
    election_deadline: datetime | None = None
    details: dict = {}
    source: str | None = None


@router.get("")
async def list_actions(status: str | None = None, user: User = StaffDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    query = select(CorporateAction).where(CorporateAction.firm_id == firm.id)
    if status:
        query = query.where(CorporateAction.status == status)
    rows = (await db.execute(query.order_by(CorporateAction.ex_date))).scalars().all()

    items = []
    for a in rows:
        inst = await db.get(Instrument, a.instrument_id)
        entitlements = (await db.execute(
            select(CorporateActionEntitlement).where(CorporateActionEntitlement.corporate_action_id == a.id)
        )).scalars().all()
        items.append({
            **_action_out(a),
            "symbol": inst.symbol if inst else None,
            "entitlement_count": len(entitlements),
            "entitlements_posted": sum(1 for e in entitlements if e.status == "posted"),
        })
    return {"items": items}


@router.post("")
async def create_action(body: ActionIn, user: User = OpsDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    if body.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=422, detail=f"action_type must be one of {ACTION_TYPES}.")
    row = CorporateAction(firm_id=firm.id, status="announced", **body.model_dump())
    db.add(row)
    await db.flush()
    return _action_out(row)


@router.post("/{action_id}/compute-entitlements")
async def compute_entitlements(action_id: uuid.UUID, user: User = OpsDep, firm: Firm = Depends(current_firm),
                                db: AsyncSession = Depends(get_db)):
    action = (await db.execute(
        select(CorporateAction).where(CorporateAction.id == action_id, CorporateAction.firm_id == firm.id)
    )).scalar_one_or_none()
    if action is None:
        raise HTTPException(status_code=404, detail="Corporate action not found.")
    try:
        created = await engine.compute_entitlements(db, action_id)
    except CorporateActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"created": [_entitlement_out(e) for e in created]}


@router.get("/{action_id}/entitlements")
async def list_entitlements(action_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                             db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CorporateActionEntitlement).where(
            CorporateActionEntitlement.corporate_action_id == action_id,
            CorporateActionEntitlement.firm_id == firm.id,
        )
    )).scalars().all()
    return {"items": [_entitlement_out(e) for e in rows]}


class ElectionIn(BaseModel):
    choice: str


@router.post("/entitlements/{entitlement_id}/elect")
async def elect_entitlement(entitlement_id: uuid.UUID, body: ElectionIn, user: User = StaffDep,
                             firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        row = await engine.elect(db, entitlement_id, choice=body.choice, actor=user.email)
    except CorporateActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _entitlement_out(row)


class PostIn(BaseModel):
    cash_amount: float | None = None
    share_amount: float | None = None
    cost_basis_adjustment: dict | None = None


@router.post("/entitlements/{entitlement_id}/post")
async def post_entitlement(entitlement_id: uuid.UUID, body: PostIn, user: User = OpsDep,
                            firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        row = await engine.post_entitlement(
            db, entitlement_id, cash_amount=body.cash_amount, share_amount=body.share_amount,
            cost_basis_adjustment=body.cost_basis_adjustment,
        )
    except CorporateActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _entitlement_out(row)
