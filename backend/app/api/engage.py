"""Advise & engage API — meetings (prep + companion), tasks, and client reports."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.aurea_core import firm_time, reports as report_core, tasks as task_core
from app.core.db import get_db, utcnow
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.client_experience import Message
from app.models.engagement import Meeting
from app.models.enums import AgentKey, MessageAuthor
from app.models.governance import Recommendation
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/engage", tags=["engage"], dependencies=[Depends(staff_user)])


def _meeting_dict(m: Meeting, household_name: str | None = None) -> dict:
    return {
        "id": str(m.id), "household_id": str(m.household_id), "household_name": household_name,
        "title": m.title, "status": m.status,
        "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
        "date_passed": m.date_passed(utcnow()),
        "brief": m.brief, "transcript": m.transcript, "notes": m.notes,
    }


async def _latest_rec(db, subject_id, agent_key) -> str | None:
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.subject_id == subject_id,
                                         Recommendation.agent_key == agent_key)
            .order_by(Recommendation.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return str(rec.id) if rec else None


# ── Meetings ──────────────────────────────────────────────────────────────────
class MeetingCreate(BaseModel):
    household_id: uuid.UUID
    title: str
    scheduled_at: datetime | None = None


class TranscriptIn(BaseModel):
    transcript: str


@router.get("/meetings")
async def list_meetings(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Meeting).where(Meeting.firm_id == firm.id).order_by(Meeting.created_at.desc()))
    ).scalars().all()
    names = {h.id: h.name for h in (await db.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    return [_meeting_dict(m, names.get(m.household_id)) for m in rows]


@router.post("/meetings")
async def create_meeting(body: MeetingCreate, user: User = Depends(require_roles(*STAFF_ROLES)),
                         firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    m = Meeting(firm_id=firm.id, household_id=body.household_id, title=body.title, scheduled_at=body.scheduled_at)
    db.add(m)
    await db.flush()
    return _meeting_dict(m)


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    m = await db.get(Meeting, meeting_id)
    if not m or m.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Meeting not found")
    hh = await db.get(Household, m.household_id)
    out = _meeting_dict(m, hh.name if hh else None)
    out["prep_recommendation_id"] = await _latest_rec(db, m.household_id, AgentKey.MEETING_PREP)
    out["companion_recommendation_id"] = await _latest_rec(db, m.household_id, AgentKey.MEETING_COMPANION)
    return out


@router.post("/meetings/{meeting_id}/transcript")
async def set_transcript(meeting_id: uuid.UUID, body: TranscriptIn,
                         user: User = Depends(require_roles(*STAFF_ROLES)),
                         firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    m = await db.get(Meeting, meeting_id)
    if not m or m.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Meeting not found")
    m.transcript = body.transcript
    await db.flush()
    return {"ok": True}


async def _run(db, firm, meeting, agent_key):
    try:
        run = await run_agent(db, firm=firm, agent_key=agent_key,
                              subject=Subject("meeting", meeting.id, meeting.title), trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {"run_id": str(run.id), "status": run.status,
            "recommendation_id": str(recs[0].id) if recs else None}


@router.post("/meetings/{meeting_id}/prep")
async def run_prep(meeting_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    m = await db.get(Meeting, meeting_id)
    if not m or m.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return await _run(db, firm, m, AgentKey.MEETING_PREP)


@router.post("/meetings/{meeting_id}/companion")
async def run_companion(meeting_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    m = await db.get(Meeting, meeting_id)
    if not m or m.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not m.transcript:
        raise HTTPException(status_code=400, detail="Add a transcript first.")
    return await _run(db, firm, m, AgentKey.MEETING_COMPANION)


# ── Tasks ─────────────────────────────────────────────────────────────────────
class TaskAssignIn(BaseModel):
    assigned_to: uuid.UUID | None = None


@router.get("/tasks")
async def list_tasks(
    status: str = "open", assigned_to_me: bool = False,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await task_core.for_firm(db, firm.id, status=status, assigned_to=user.id if assigned_to_me else None)
    return await task_core.views(db, rows, firm_time.today(firm))


@router.get("/task-assignees")
async def task_assignees(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Who a task can be given to. Any staff role may assign, so this is not the admin user list."""
    return [{"id": str(u.id), "name": u.full_name or u.email, "role": u.role}
            for u in await task_core.staff(db, firm.id)]


@router.patch("/tasks/{task_id}")
async def assign_task(
    task_id: uuid.UUID, body: TaskAssignIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    try:
        t = await task_core.get(db, firm.id, task_id)
        to = await task_core.staff_member(db, firm.id, body.assigned_to) if body.assigned_to else None
        task_core.assign(t, to, user.id)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))
    await db.flush()
    return {"ok": True, "assigned_to": str(body.assigned_to) if body.assigned_to else None}


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: uuid.UUID, user: User = Depends(require_roles(*STAFF_ROLES)),
                        firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        task_core.complete(await task_core.get(db, firm.id, task_id))
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))
    await db.flush()
    return {"ok": True}


# ── Adviser message inbox ─────────────────────────────────────────────────────
@router.get("/messages")
async def message_inbox(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Per-household message threads with unread (client→adviser) counts."""
    msgs = (
        await db.execute(select(Message).where(Message.firm_id == firm.id).order_by(Message.created_at.desc()))
    ).scalars().all()
    names = {h.id: h.name for h in (await db.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    threads: dict = {}
    for m in msgs:
        t = threads.setdefault(m.household_id, {"household_id": str(m.household_id),
                                                "household_name": names.get(m.household_id),
                                                "last_body": m.body, "last_at": m.created_at.isoformat() if m.created_at else None,
                                                "unread": 0})
        if m.author_role == MessageAuthor.CLIENT and not m.read_by_adviser:
            t["unread"] += 1
    return list(threads.values())


# ── Client reports ────────────────────────────────────────────────────────────
@router.get("/reports")
async def list_reports(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await report_core.views(db, firm.id, await report_core.for_firm(db, firm.id))


@router.get("/reports/{report_id}")
async def get_report(report_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        r = await report_core.get(db, firm.id, report_id)
    except report_core.ReportError:
        raise HTTPException(status_code=404, detail="Report not found")
    return (await report_core.views(db, firm.id, [r], full=True))[0]
