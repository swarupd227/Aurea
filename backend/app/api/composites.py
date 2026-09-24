"""GIPS composite reporting API (L200-5 §2.5)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import composites as engine
from app.core.db import get_db
from app.core.decision_rights import STAFF
from app.core.security import UserRole, require_roles
from app.models.composites import COMPOSITE_STATUSES, Composite
from app.models.identity import User
from app.models.portfolio import ModelPortfolio
from app.models.tenant import Firm

router = APIRouter(prefix="/api/composites", tags=["composites"])
StaffDep = Depends(require_roles(*STAFF))
AdminDep = Depends(require_roles(UserRole.ADMIN, UserRole.RESEARCH_CIO, UserRole.COMPLIANCE))


def _composite_out(c: Composite) -> dict:
    return {
        "id": str(c.id), "model_portfolio_id": str(c.model_portfolio_id), "name": c.name,
        "inclusion_criteria": c.inclusion_criteria, "creation_date": c.creation_date.isoformat(),
        "seasoning_days": c.seasoning_days,
        "minimum_account_size": float(c.minimum_account_size) if c.minimum_account_size is not None else None,
        "status": c.status,
    }


@router.get("")
async def list_composites(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Composite).where(Composite.firm_id == firm.id).order_by(Composite.name))).scalars().all()
    return {"items": [_composite_out(c) for c in rows]}


class CompositeIn(BaseModel):
    model_portfolio_id: uuid.UUID
    name: str
    inclusion_criteria: str
    creation_date: date
    seasoning_days: int = 90
    minimum_account_size: float | None = None


@router.post("")
async def create_composite(body: CompositeIn, user: User = AdminDep, firm: Firm = Depends(current_firm),
                            db: AsyncSession = Depends(get_db)):
    model = await db.get(ModelPortfolio, body.model_portfolio_id)
    if model is None or model.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Model portfolio not found.")
    row = Composite(firm_id=firm.id, status="active", **body.model_dump())
    db.add(row)
    await db.flush()
    return _composite_out(row)


@router.get("/{composite_id}/report")
async def get_report(composite_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    composite = (await db.execute(
        select(Composite).where(Composite.id == composite_id, Composite.firm_id == firm.id)
    )).scalar_one_or_none()
    if composite is None:
        raise HTTPException(status_code=404, detail="Composite not found.")
    return await engine.composite_report(db, composite)


@router.get("/firm-definition-check")
async def get_firm_definition_check(user: User = StaffDep, firm: Firm = Depends(current_firm),
                                     db: AsyncSession = Depends(get_db)):
    return await engine.firm_definition_check(db, firm.id)
