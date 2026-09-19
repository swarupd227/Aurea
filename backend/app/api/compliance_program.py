"""Conflicts inventory + WSP grid API (L200-7 §2.2, §3.1) — firm-editable compliance
artifacts, distinct from the deterministic rules engine at /api/admin/compliance."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import compliance_program as engine
from app.core.db import get_db, utcnow
from app.core.security import UserRole, require_roles
from app.models.compliance_program import CONFLICT_STATUSES, ConflictInventoryItem, WSPRule
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/compliance-program", tags=["compliance-program"])
ProgramDep = Depends(require_roles(UserRole.ADMIN, UserRole.COMPLIANCE))


def _conflict_out(c: ConflictInventoryItem) -> dict:
    return {
        "id": str(c.id), "conflict_key": c.conflict_key, "title": c.title,
        "mechanism_of_harm": c.mechanism_of_harm, "mitigation": c.mitigation,
        "disclosure": c.disclosure, "owner": c.owner, "status": c.status,
        "review_cadence_days": c.review_cadence_days,
        "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
        "last_reviewed_by": c.last_reviewed_by, "notes": c.notes,
    }


def _wsp_out(r: WSPRule) -> dict:
    return {
        "id": str(r.id), "rule_key": r.rule_key, "ontology_rule_id": r.ontology_rule_id,
        "obligation": r.obligation, "designated_role": r.designated_role,
        "supervisory_activity": r.supervisory_activity, "frequency": r.frequency,
        "evidence_description": r.evidence_description, "evidence_automated": r.evidence_automated,
        "last_evidence_at": r.last_evidence_at.isoformat() if r.last_evidence_at else None,
        "is_active": r.is_active, "change_log": r.change_log,
    }


# ── Conflicts inventory ─────────────────────────────────────────────────────────

class ConflictIn(BaseModel):
    conflict_key: str
    title: str
    mechanism_of_harm: str
    mitigation: str
    disclosure: str
    owner: str | None = None
    review_cadence_days: int = 365
    notes: str | None = None


class ConflictUpdate(BaseModel):
    title: str | None = None
    mechanism_of_harm: str | None = None
    mitigation: str | None = None
    disclosure: str | None = None
    owner: str | None = None
    status: str | None = None
    review_cadence_days: int | None = None
    notes: str | None = None
    mark_reviewed: bool = False


@router.get("/conflicts")
async def list_conflicts(user: User = ProgramDep, firm: Firm = Depends(current_firm),
                          db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ConflictInventoryItem).where(ConflictInventoryItem.firm_id == firm.id)
        .order_by(ConflictInventoryItem.title)
    )).scalars().all()
    return {"items": [_conflict_out(c) for c in rows]}


@router.post("/conflicts")
async def create_conflict(body: ConflictIn, user: User = ProgramDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(
        select(ConflictInventoryItem).where(
            ConflictInventoryItem.firm_id == firm.id,
            ConflictInventoryItem.conflict_key == body.conflict_key,
        )
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"Conflict key '{body.conflict_key}' already exists.")
    row = ConflictInventoryItem(firm_id=firm.id, status="active", **body.model_dump())
    db.add(row)
    await db.flush()
    return _conflict_out(row)


@router.patch("/conflicts/{conflict_id}")
async def update_conflict(conflict_id: uuid.UUID, body: ConflictUpdate, user: User = ProgramDep,
                           firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(ConflictInventoryItem).where(
            ConflictInventoryItem.id == conflict_id, ConflictInventoryItem.firm_id == firm.id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Conflict inventory item not found.")

    if body.status is not None and body.status not in CONFLICT_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {CONFLICT_STATUSES}.")

    for field in ("title", "mechanism_of_harm", "mitigation", "disclosure", "owner",
                  "status", "review_cadence_days", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    if body.mark_reviewed:
        row.last_reviewed_at = utcnow()
        row.last_reviewed_by = user.email

    await db.flush()
    return _conflict_out(row)


# ── WSP grid ─────────────────────────────────────────────────────────────────────

class WSPIn(BaseModel):
    rule_key: str
    ontology_rule_id: str | None = None
    obligation: str
    designated_role: str
    supervisory_activity: str
    frequency: str
    evidence_description: str
    evidence_automated: bool = False


class WSPUpdate(BaseModel):
    obligation: str | None = None
    designated_role: str | None = None
    supervisory_activity: str | None = None
    frequency: str | None = None
    evidence_description: str | None = None
    evidence_automated: bool | None = None
    is_active: bool | None = None
    log_evidence: bool = False


@router.get("/wsp")
async def list_wsp(user: User = ProgramDep, firm: Firm = Depends(current_firm),
                    db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(WSPRule).where(WSPRule.firm_id == firm.id).order_by(WSPRule.rule_key)
    )).scalars().all()
    return {"items": [_wsp_out(r) for r in rows]}


@router.post("/wsp")
async def create_wsp(body: WSPIn, user: User = ProgramDep, firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(
        select(WSPRule).where(WSPRule.firm_id == firm.id, WSPRule.rule_key == body.rule_key)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"WSP rule key '{body.rule_key}' already exists.")
    row = WSPRule(firm_id=firm.id, is_active=True, change_log=[], **body.model_dump())
    db.add(row)
    await db.flush()
    return _wsp_out(row)


@router.patch("/wsp/{wsp_id}")
async def update_wsp(wsp_id: uuid.UUID, body: WSPUpdate, user: User = ProgramDep,
                      firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(WSPRule).where(WSPRule.id == wsp_id, WSPRule.firm_id == firm.id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="WSP rule not found.")

    changed: list[str] = []
    for field in ("obligation", "designated_role", "supervisory_activity", "frequency",
                  "evidence_description", "evidence_automated", "is_active"):
        value = getattr(body, field)
        if value is not None and value != getattr(row, field):
            setattr(row, field, value)
            changed.append(field)

    if changed:
        # §3.1: threshold/scenario-style changes are versioned with approval — a quietly
        # raised threshold is the finding that turns tuning into an enforcement narrative.
        log = list(row.change_log or [])
        log.append({"at": utcnow().isoformat(), "by": user.email, "changed": changed})
        row.change_log = log

    if body.log_evidence:
        row.last_evidence_at = utcnow()

    await db.flush()
    return _wsp_out(row)


# ── Summary (feeds L200-7 §10's metric catalog) ──────────────────────────────────

@router.get("/summary")
async def program_summary(user: User = ProgramDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    return {
        "conflicts": await engine.conflicts_summary(db, firm.id),
        "evidence_coverage": await engine.evidence_coverage(db, firm.id),
    }
