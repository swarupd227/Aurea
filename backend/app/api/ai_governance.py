"""AI use-case inventory, change log, and entitlement-violation API (L200-8 §8)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import ai_governance as engine
from app.core.db import get_db, utcnow
from app.core.security import UserRole, require_roles
from app.models.ai_governance import (
    CHANGE_TYPES, RISK_TIERS, USE_CASE_CATEGORIES, AIChangeLogEntry, AIUseCase, EntitlementViolation,
)
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/admin/ai-governance", tags=["ai-governance"])
AdminDep = Depends(require_roles(UserRole.ADMIN, UserRole.COMPLIANCE))


def _use_case_out(uc: AIUseCase) -> dict:
    return {
        "id": str(uc.id), "use_case_key": uc.use_case_key, "name": uc.name, "category": uc.category,
        "description": uc.description, "owner": uc.owner, "risk_tier": uc.risk_tier, "status": uc.status,
        "review_cadence_days": uc.review_cadence_days,
        "last_reviewed_at": uc.last_reviewed_at.isoformat() if uc.last_reviewed_at else None,
    }


@router.get("/summary")
async def get_summary(user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await engine.inventory_summary(db, firm.id)


@router.get("/use-cases")
async def list_use_cases(user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(AIUseCase).where(AIUseCase.firm_id == firm.id).order_by(AIUseCase.risk_tier.desc(), AIUseCase.name)
    )).scalars().all()
    return {"items": [_use_case_out(u) for u in rows]}


class UseCaseIn(BaseModel):
    use_case_key: str
    name: str
    category: str = "agent"
    description: str
    owner: str
    risk_tier: str = "medium"
    review_cadence_days: int = 180


@router.post("/use-cases")
async def create_use_case(body: UseCaseIn, user: User = AdminDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    if body.category not in USE_CASE_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {USE_CASE_CATEGORIES}.")
    if body.risk_tier not in RISK_TIERS:
        raise HTTPException(status_code=422, detail=f"risk_tier must be one of {RISK_TIERS}.")
    row = AIUseCase(firm_id=firm.id, status="active", **body.model_dump())
    db.add(row)
    await db.flush()
    return _use_case_out(row)


class UseCaseUpdate(BaseModel):
    owner: str | None = None
    risk_tier: str | None = None
    status: str | None = None
    mark_reviewed: bool = False


@router.patch("/use-cases/{use_case_id}")
async def update_use_case(use_case_id: uuid.UUID, body: UseCaseUpdate, user: User = AdminDep,
                           firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(AIUseCase).where(AIUseCase.id == use_case_id, AIUseCase.firm_id == firm.id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Use case not found.")
    if body.risk_tier is not None and body.risk_tier not in RISK_TIERS:
        raise HTTPException(status_code=422, detail=f"risk_tier must be one of {RISK_TIERS}.")

    changed = []
    if body.owner is not None and body.owner != row.owner:
        changed.append(f"owner: {row.owner} -> {body.owner}")
        row.owner = body.owner
    if body.risk_tier is not None and body.risk_tier != row.risk_tier:
        changed.append(f"risk_tier: {row.risk_tier} -> {body.risk_tier}")
        row.risk_tier = body.risk_tier
    if body.status is not None:
        row.status = body.status
    if body.mark_reviewed:
        row.last_reviewed_at = utcnow()

    if changed:
        await engine.log_change(
            db, firm.id, use_case_id=row.id, change_type="risk_tier",
            description=f"{row.name}: " + "; ".join(changed), changed_by=user.email,
        )
    await db.flush()
    return _use_case_out(row)


@router.get("/violations")
async def list_violations(user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(EntitlementViolation).where(EntitlementViolation.firm_id == firm.id)
        .order_by(EntitlementViolation.occurred_at.desc()).limit(100)
    )).scalars().all()
    return {"items": [{
        "id": str(v.id), "occurred_at": v.occurred_at.isoformat(), "actor_role": v.actor_role,
        "violation_type": v.violation_type, "subject_key": v.subject_key, "detail": v.detail,
        "blocked": v.blocked,
    } for v in rows]}


class ChangeIn(BaseModel):
    use_case_id: uuid.UUID | None = None
    change_type: str = "other"
    description: str
    previous_value: str | None = None
    new_value: str | None = None


@router.get("/changes")
async def list_changes(user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(AIChangeLogEntry).where(AIChangeLogEntry.firm_id == firm.id)
        .order_by(AIChangeLogEntry.changed_at.desc()).limit(100)
    )).scalars().all()
    return {"items": [{
        "id": str(c.id), "use_case_id": str(c.use_case_id) if c.use_case_id else None,
        "change_type": c.change_type, "description": c.description, "previous_value": c.previous_value,
        "new_value": c.new_value, "changed_by": c.changed_by, "changed_at": c.changed_at.isoformat(),
    } for c in rows]}


@router.post("/changes")
async def create_change(body: ChangeIn, user: User = AdminDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    if body.change_type not in CHANGE_TYPES:
        raise HTTPException(status_code=422, detail=f"change_type must be one of {CHANGE_TYPES}.")
    row = await engine.log_change(
        db, firm.id, use_case_id=body.use_case_id, change_type=body.change_type,
        description=body.description, changed_by=user.email,
        previous_value=body.previous_value, new_value=body.new_value,
    )
    return {"id": str(row.id), "changed_at": row.changed_at.isoformat()}
