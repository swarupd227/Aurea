"""Model portfolio CRUD — target allocations that mandate drift is measured against (G1)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.core.db import get_db
from app.core.security import STAFF_ROLES, require_roles, staff_user
from app.models.enums import AssetClass
from app.models.identity import User
from app.models.portfolio import ModelPortfolio, TargetAllocation
from app.models.tenant import Firm

router = APIRouter(
    prefix="/api/admin/model-portfolios",
    tags=["model-portfolios"],
    dependencies=[Depends(staff_user)],
)

AdminDep = Depends(require_roles(*STAFF_ROLES))


class ModelPortfolioIn(BaseModel):
    name: str
    description: str | None = None
    drift_band: float = 0.05


class TargetWeightIn(BaseModel):
    asset_class: str
    target_weight: float  # fraction (0.0–1.0)


def _mp_dict(mp: ModelPortfolio, targets: list[TargetAllocation] | None = None) -> dict:
    return {
        "id": str(mp.id),
        "name": mp.name,
        "description": mp.description,
        "drift_band": float(mp.drift_band),
        "targets": [
            {"asset_class": t.asset_class, "target_weight": float(t.target_weight)}
            for t in (targets or [])
        ],
        "created_at": mp.created_at.isoformat() if mp.created_at else None,
    }


@router.get("")
async def list_model_portfolios(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
            .order_by(ModelPortfolio.created_at)
        )
    ).scalars().all()
    # Load targets per model
    if rows:
        alloc_rows = (
            await db.execute(
                select(TargetAllocation).where(
                    TargetAllocation.model_id.in_([mp.id for mp in rows])
                )
            )
        ).scalars().all()
        by_model: dict[uuid.UUID, list[TargetAllocation]] = {}
        for t in alloc_rows:
            by_model.setdefault(t.model_id, []).append(t)
    else:
        by_model = {}
    return [_mp_dict(mp, by_model.get(mp.id, [])) for mp in rows]


@router.post("")
async def create_model_portfolio(
    body: ModelPortfolioIn,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    mp = ModelPortfolio(
        firm_id=firm.id, name=body.name, description=body.description,
        drift_band=body.drift_band,
    )
    db.add(mp)
    await db.flush()
    return _mp_dict(mp)


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
    mp = await db.get(ModelPortfolio, model_id)
    if not mp or mp.firm_id != firm.id:
        raise HTTPException(404, "Model portfolio not found.")
    mp.name = body.name
    if body.description is not None:
        mp.description = body.description
    mp.drift_band = body.drift_band
    await db.flush()
    return {"ok": True}


@router.delete("/{model_id}")
async def delete_model_portfolio(
    model_id: uuid.UUID,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    mp = await db.get(ModelPortfolio, model_id)
    if not mp or mp.firm_id != firm.id:
        raise HTTPException(404, "Model portfolio not found.")
    await db.delete(mp)
    return {"ok": True}


@router.put("/{model_id}/targets")
async def set_targets(
    model_id: uuid.UUID, targets: list[TargetWeightIn],
    user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Replace the full target weight set (must sum to 1.0 ± 0.005)."""
    mp = await db.get(ModelPortfolio, model_id)
    if not mp or mp.firm_id != firm.id:
        raise HTTPException(404, "Model portfolio not found.")

    total = sum(t.target_weight for t in targets)
    if targets and not (0.995 <= total <= 1.005):
        raise HTTPException(400, f"Weights must sum to 1.0 (got {total:.4f}).")

    # Delete existing
    existing = (
        await db.execute(select(TargetAllocation).where(TargetAllocation.model_id == model_id))
    ).scalars().all()
    for ta in existing:
        await db.delete(ta)

    # Insert new
    new_targets = []
    for t in targets:
        ta = TargetAllocation(
            firm_id=firm.id, model_id=model_id,
            asset_class=t.asset_class, target_weight=t.target_weight,
        )
        db.add(ta)
        new_targets.append(ta)
    await db.flush()
    return {"ok": True, "targets": len(new_targets), "total_weight": round(total, 4)}
