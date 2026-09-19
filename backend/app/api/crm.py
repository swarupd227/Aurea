"""CRM pipeline API (L200-8 §2.1) — contacts, opportunities and the activity log, kept
structurally separate from the client graph until a deal converts."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import crm as engine
from app.core.decision_rights import STAFF
from app.core.db import get_db, utcnow
from app.core.security import require_roles
from app.models.crm import (
    ACTIVITY_TYPES, CONTACT_TYPES, PIPELINE_STAGES, CrmActivity, CrmContact, CrmOpportunity,
)
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/crm", tags=["crm"])
# Prospecting is staff work, not restricted to admin/compliance — but never a client (a
# client already sees only their own household and holds no state-changing tools).
CrmDep = Depends(require_roles(*STAFF))


def _contact_out(c: CrmContact) -> dict:
    return {
        "id": str(c.id), "full_name": c.full_name, "email": c.email, "phone": c.phone,
        "contact_type": c.contact_type, "household_id": str(c.household_id) if c.household_id else None,
        "source": c.source, "owner_user_id": str(c.owner_user_id) if c.owner_user_id else None,
        "notes": c.notes, "is_active": c.is_active,
    }


def _opp_out(o: CrmOpportunity) -> dict:
    return {
        "id": str(o.id), "contact_id": str(o.contact_id), "title": o.title, "stage": o.stage,
        "estimated_aum": float(o.estimated_aum) if o.estimated_aum is not None else None,
        "probability_pct": o.probability_pct,
        "expected_close_date": o.expected_close_date.isoformat() if o.expected_close_date else None,
        "lost_reason": o.lost_reason,
        "opened_at": o.opened_at.isoformat() if o.opened_at else None,
        "closed_at": o.closed_at.isoformat() if o.closed_at else None,
        "converted_household_id": str(o.converted_household_id) if o.converted_household_id else None,
    }


def _activity_out(a: CrmActivity) -> dict:
    return {
        "id": str(a.id), "contact_id": str(a.contact_id),
        "opportunity_id": str(a.opportunity_id) if a.opportunity_id else None,
        "activity_type": a.activity_type, "occurred_at": a.occurred_at.isoformat(),
        "detail": a.detail, "logged_by_user_id": str(a.logged_by_user_id) if a.logged_by_user_id else None,
    }


# ── Contacts ─────────────────────────────────────────────────────────────────────

class ContactIn(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    contact_type: str = "prospect"
    source: str | None = None
    notes: str | None = None


@router.get("/contacts")
async def list_contacts(user: User = CrmDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CrmContact).where(CrmContact.firm_id == firm.id).order_by(CrmContact.full_name)
    )).scalars().all()
    return {"items": [_contact_out(c) for c in rows]}


@router.post("/contacts")
async def create_contact(body: ContactIn, user: User = CrmDep, firm: Firm = Depends(current_firm),
                          db: AsyncSession = Depends(get_db)):
    if body.contact_type not in CONTACT_TYPES:
        raise HTTPException(status_code=422, detail=f"contact_type must be one of {CONTACT_TYPES}.")
    row = CrmContact(firm_id=firm.id, owner_user_id=user.id, is_active=True, **body.model_dump())
    db.add(row)
    await db.flush()
    return _contact_out(row)


@router.post("/contacts/{contact_id}/convert")
async def convert_contact(contact_id: uuid.UUID, household_id: uuid.UUID, user: User = CrmDep,
                           firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Link a contact to the household it became — the one deliberate seam into the client
    graph (L200-8 §2.1 keeps CRM and client-graph structurally separate otherwise)."""
    contact = (await db.execute(
        select(CrmContact).where(CrmContact.id == contact_id, CrmContact.firm_id == firm.id)
    )).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found.")
    household = (await db.execute(
        select(Household).where(Household.id == household_id, Household.firm_id == firm.id)
    )).scalar_one_or_none()
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    contact.household_id = household.id
    contact.contact_type = "client_contact"
    await db.flush()
    return _contact_out(contact)


# ── Opportunities ────────────────────────────────────────────────────────────────

class OpportunityIn(BaseModel):
    contact_id: uuid.UUID
    title: str
    estimated_aum: float | None = None
    probability_pct: int = 10
    expected_close_date: date | None = None


class OpportunityUpdate(BaseModel):
    stage: str | None = None
    estimated_aum: float | None = None
    probability_pct: int | None = None
    expected_close_date: date | None = None
    lost_reason: str | None = None
    converted_household_id: uuid.UUID | None = None


@router.get("/opportunities")
async def list_opportunities(user: User = CrmDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CrmOpportunity).where(CrmOpportunity.firm_id == firm.id).order_by(CrmOpportunity.opened_at.desc())
    )).scalars().all()
    return {"items": [_opp_out(o) for o in rows]}


@router.post("/opportunities")
async def create_opportunity(body: OpportunityIn, user: User = CrmDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    contact = (await db.execute(
        select(CrmContact).where(CrmContact.id == body.contact_id, CrmContact.firm_id == firm.id)
    )).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found.")
    row = CrmOpportunity(firm_id=firm.id, stage="lead", opened_at=utcnow(), **body.model_dump())
    db.add(row)
    await db.flush()
    return _opp_out(row)


@router.patch("/opportunities/{opportunity_id}")
async def update_opportunity(opportunity_id: uuid.UUID, body: OpportunityUpdate, user: User = CrmDep,
                              firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(CrmOpportunity).where(CrmOpportunity.id == opportunity_id, CrmOpportunity.firm_id == firm.id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")

    if body.stage is not None:
        if body.stage not in PIPELINE_STAGES:
            raise HTTPException(status_code=422, detail=f"stage must be one of {PIPELINE_STAGES}.")
        row.stage = body.stage
        if body.stage in ("won", "lost") and row.closed_at is None:
            row.closed_at = utcnow()

    for field in ("estimated_aum", "probability_pct", "expected_close_date", "lost_reason",
                  "converted_household_id"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    await db.flush()
    return _opp_out(row)


# ── Activity log ─────────────────────────────────────────────────────────────────

class ActivityIn(BaseModel):
    contact_id: uuid.UUID
    opportunity_id: uuid.UUID | None = None
    activity_type: str = "note"
    occurred_at: datetime | None = None
    detail: str


@router.get("/contacts/{contact_id}/activity")
async def list_activity(contact_id: uuid.UUID, user: User = CrmDep, firm: Firm = Depends(current_firm),
                         db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CrmActivity).where(CrmActivity.contact_id == contact_id, CrmActivity.firm_id == firm.id)
        .order_by(CrmActivity.occurred_at.desc())
    )).scalars().all()
    return {"items": [_activity_out(a) for a in rows]}


@router.post("/activity")
async def log_activity(body: ActivityIn, user: User = CrmDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    if body.activity_type not in ACTIVITY_TYPES:
        raise HTTPException(status_code=422, detail=f"activity_type must be one of {ACTIVITY_TYPES}.")
    contact = (await db.execute(
        select(CrmContact).where(CrmContact.id == body.contact_id, CrmContact.firm_id == firm.id)
    )).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found.")
    data = body.model_dump()
    data["occurred_at"] = data["occurred_at"] or utcnow()
    row = CrmActivity(firm_id=firm.id, logged_by_user_id=user.id, **data)
    db.add(row)
    await db.flush()
    return _activity_out(row)


# ── Pipeline summary ─────────────────────────────────────────────────────────────

@router.get("/pipeline-summary")
async def pipeline_summary(user: User = CrmDep, firm: Firm = Depends(current_firm),
                            db: AsyncSession = Depends(get_db)):
    return await engine.pipeline_summary(db, firm.id)
