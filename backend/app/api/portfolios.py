"""Model portfolio CRUD — target allocations that mandate drift is measured against (G1)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import platform_admin
from app.aurea_core.platform_admin import AdminError
from app.core.db import get_db
from app.core.security import require_roles, staff_user
from app.models.enums import UserRole
from app.models.enums import AssetClass
from app.models.identity import User
from app.models.portfolio import ModelPortfolio, TargetAllocation
from app.models.tenant import Firm

router = APIRouter(
    prefix="/api/admin/model-portfolios",
    tags=["model-portfolios"],
    dependencies=[Depends(staff_user)],
)

# A model drives every rebalance on the mandates that follow it: the portfolio team and research build it,
# and the admin may (B-96). Every staff role could create, retarget or delete one before.
AdminDep = Depends(require_roles(UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO))


class ModelPortfolioIn(BaseModel):
    name: str
    description: str | None = None
    drift_band: float = 0.05
    benchmark_symbol: str | None = None


class TargetWeightIn(BaseModel):
    asset_class: str
    target_weight: float  # fraction (0.0–1.0)


def _mp_dict(mp: ModelPortfolio, targets: list[TargetAllocation] | None = None) -> dict:
    return platform_admin.model_out(mp, targets)


def _refused(exc: AdminError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
async def list_model_portfolios(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_model_portfolios(db, firm)


@router.post("")
async def create_model_portfolio(
    body: ModelPortfolioIn,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    try:
        done = await platform_admin.save_model_portfolio(db, firm, user, name=body.name, description=body.description,
                                                         drift_band=body.drift_band, benchmark_symbol=body.benchmark_symbol)
    except AdminError as exc:
        raise _refused(exc)
    return _mp_dict(done.record)


@router.get("/{model_id}")
async def get_model_portfolio(
    model_id: uuid.UUID,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    mp = await db.get(ModelPortfolio, model_id)
    if not mp or mp.firm_id != firm.id:
        raise HTTPException(404, "Model portfolio not found.")
    targets = (
        await db.execute(
            select(TargetAllocation).where(TargetAllocation.model_id == model_id)
        )
    ).scalars().all()
    return _mp_dict(mp, list(targets))


@router.patch("/{model_id}")
async def update_model_portfolio(
    model_id: uuid.UUID, body: ModelPortfolioIn,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    # Ledgered: a composite on this model must disclose a benchmark change (aurea_core/composites).
    try:
        await platform_admin.save_model_portfolio(db, firm, user, model_id=model_id, name=body.name,
                                                  description=body.description, drift_band=body.drift_band,
                                                  benchmark_symbol=body.benchmark_symbol)
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True}


@router.delete("/{model_id}")
async def delete_model_portfolio(
    model_id: uuid.UUID,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    # Deleting cascades to its composites and sleeves and leaves its mandates with no model; refused while any follows it.
    try:
        await platform_admin.delete_model_portfolio(db, firm, user, model_id)
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True}


@router.put("/{model_id}/targets")
async def set_targets(
    model_id: uuid.UUID, targets: list[TargetWeightIn],
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Replace the full target weight set (must sum to 1.0 ± 0.005)."""
    try:
        done = await platform_admin.set_model_targets(db, firm, user, model_id, [t.model_dump() for t in targets])
    except AdminError as exc:
        raise _refused(exc)
    total = sum(t.target_weight for t in targets)
    return {"ok": True, "targets": len(targets), "total_weight": round(total, 4)}
