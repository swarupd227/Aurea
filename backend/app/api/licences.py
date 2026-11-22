"""Licences and client consent.

Reading is for staff. Recording a client's answer is for advisers and paraplanners, who hold the
client relationship; it is not a client action, and it is not an approval (runtime.decide is that).
The agent-facing tool is the same action behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.atlas import runtime
from app.aurea_core import compliance_calendar, consent, licences, pre_advice
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/licences", tags=["licences"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN))
RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})


class AnswerIn(BaseModel):
    given: bool
    channel: str
    evidence_ref: str | None = None
    note: str | None = None


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await licences.overview(db, firm), "can_record": user.role in RECORDERS}


@router.post("/consents/{recommendation_id}")
async def answer(recommendation_id: uuid.UUID, body: AnswerIn, user: User = ReaderDep,
                 firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    if user.role not in RECORDERS:
        raise HTTPException(status_code=403, detail="Recording a client's answer is for advisers and paraplanners.")
    rec = (await db.execute(select(Recommendation).where(
        Recommendation.id == recommendation_id, Recommendation.firm_id == firm.id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    try:
        rec = await runtime.client_response(
            db, firm=firm, recommendation=rec, given=body.given, channel=body.channel,
            evidence_ref=body.evidence_ref, note=body.note, actor_label=user.email)
    except consent.ConsentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except runtime.AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return {"id": str(rec.id), "status": rec.status, "client_consent": (rec.payload or {}).get("client_consent"),
            "execution_result": (rec.payload or {}).get("execution_result")}


class FilingIn(BaseModel):
    obligation: str
    period: str
    reference: str
    filed_on: date | None = None
    note: str | None = None


class RegistrationIn(BaseModel):
    details: dict
    reason: str


@router.get("/pre-advice/{household_id}")
async def before_advice(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    try:
        return await pre_advice.status(db, firm, household_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/filings")
async def record_filing(body: FilingIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    """The tool record_compliance_filing behind its Confirm card is the same action."""
    if user.role != UserRole.COMPLIANCE:
        raise HTTPException(status_code=403, detail="A filing is recorded by compliance.")
    try:
        result = await compliance_calendar.record_filing(db, firm, by=user.email, note=body.note, obligation=body.obligation,
                                                         period=body.period, filed_on=body.filed_on, reference=body.reference)
    except compliance_calendar.CalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.put("/registration")
async def record_registration(body: RegistrationIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    """The tool record_sebi_registration behind its Confirm card is the same action."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="The firm's registration and officers are its administrator's to record.")
    try:
        result = await compliance_calendar.record_officers(db, firm, user, details=body.details, reason=body.reason)
    except compliance_calendar.CalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result
