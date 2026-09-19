"""UMA sleeve API (L200-3 §6) — the logical partition, netting, and reconciliation."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import sleeves as engine
from app.aurea_core.sleeves import SleeveIntent
from app.core.decision_rights import STAFF
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.portfolio import Holding
from app.models.sleeves import SLEEVE_STATUSES, Sleeve, SleeveHolding
from app.models.tenant import Firm

router = APIRouter(prefix="/api/sleeves", tags=["sleeves"])
StaffDep = Depends(require_roles(*STAFF))
# Attributing shares between sleeves and netting trade intent is portfolio-construction
# work — the same authority level as approving a rebalance, not general staff access.
PortfolioDep = Depends(require_roles(UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN))


def _sleeve_out(s: Sleeve) -> dict:
    return {
        "id": str(s.id), "account_id": str(s.account_id), "model_id": str(s.model_id),
        "name": s.name, "target_weight": float(s.target_weight),
        "cash_target": float(s.cash_target), "status": s.status,
    }


def _attribution_out(sh: SleeveHolding) -> dict:
    return {
        "id": str(sh.id), "sleeve_id": str(sh.sleeve_id), "holding_id": str(sh.holding_id),
        "quantity": float(sh.quantity), "cost_basis": float(sh.cost_basis),
    }


class SleeveIn(BaseModel):
    account_id: uuid.UUID
    model_id: uuid.UUID
    name: str
    target_weight: float = 0.0
    cash_target: float = 0.02


@router.get("")
async def list_sleeves(account_id: uuid.UUID | None = None, user: User = StaffDep,
                        firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    query = select(Sleeve).where(Sleeve.firm_id == firm.id)
    if account_id:
        query = query.where(Sleeve.account_id == account_id)
    rows = (await db.execute(query.order_by(Sleeve.name))).scalars().all()
    return {"items": [_sleeve_out(s) for s in rows]}


@router.post("")
async def create_sleeve(body: SleeveIn, user: User = PortfolioDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    row = Sleeve(firm_id=firm.id, status="active", **body.model_dump())
    db.add(row)
    await db.flush()
    return _sleeve_out(row)


class AttributionIn(BaseModel):
    sleeve_id: uuid.UUID
    holding_id: uuid.UUID
    quantity: float
    cost_basis: float = 0.0


@router.post("/attributions")
async def set_attribution(body: AttributionIn, user: User = PortfolioDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    sleeve = (await db.execute(
        select(Sleeve).where(Sleeve.id == body.sleeve_id, Sleeve.firm_id == firm.id)
    )).scalar_one_or_none()
    if sleeve is None:
        raise HTTPException(status_code=404, detail="Sleeve not found.")
    holding = (await db.execute(
        select(Holding).where(Holding.id == body.holding_id, Holding.firm_id == firm.id)
    )).scalar_one_or_none()
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found.")
    if holding.account_id != sleeve.account_id:
        raise HTTPException(status_code=422, detail="Holding does not belong to this sleeve's account.")

    existing = (await db.execute(
        select(SleeveHolding).where(
            SleeveHolding.sleeve_id == body.sleeve_id, SleeveHolding.holding_id == body.holding_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        existing.quantity = body.quantity
        existing.cost_basis = body.cost_basis
        row = existing
    else:
        row = SleeveHolding(firm_id=firm.id, **body.model_dump())
        db.add(row)
    await db.flush()
    return _attribution_out(row)


@router.get("/accounts/{account_id}/reconcile")
async def reconcile_account(account_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                             db: AsyncSession = Depends(get_db)):
    return await engine.reconcile(db, account_id)


class IntentIn(BaseModel):
    sleeve_id: uuid.UUID
    account_id: uuid.UUID
    instrument_id: uuid.UUID
    symbol: str
    side: str
    quantity: float
    price: float = 0.0


class NetIntentsIn(BaseModel):
    intents: list[IntentIn]


@router.post("/net")
async def net_intents(body: NetIntentsIn, user: User = PortfolioDep, firm: Firm = Depends(current_firm)):
    """Cross conflicting sleeve intents on the same instrument into one net order per
    (account, instrument) — L200-3 §6.2. Stateless: takes intents, returns net orders; the
    caller decides whether to actually place them via the normal order-execution path."""
    try:
        orders = engine.net_intents([
            SleeveIntent(
                sleeve_id=str(i.sleeve_id), account_id=str(i.account_id),
                instrument_id=str(i.instrument_id), symbol=i.symbol, side=i.side,
                quantity=i.quantity, price=i.price,
            ) for i in body.intents
        ])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"net_orders": [
        {
            "account_id": o.account_id, "instrument_id": o.instrument_id, "symbol": o.symbol,
            "side": o.side, "quantity": o.quantity, "gross_buy_quantity": o.gross_buy_quantity,
            "gross_sell_quantity": o.gross_sell_quantity, "crossed_quantity": o.crossed_quantity,
            "sleeve_allocations": o.sleeve_allocations,
        } for o in orders
    ]}
