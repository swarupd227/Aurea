"""Skills API — author, edit, share, test and run advisor-defined skills (governed via Atlas).

Each adviser has their own workspace: a skill is owned by its creator and is private by default; it
can be shared with named colleagues or made public to the whole firm. Anyone who can see a skill can
run, test or clone it; only the owner can edit, share or delete it."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.core.db import get_db
from app.core.security import staff_user
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.skill import Skill
from app.models.tenant import Firm
from app.skills import library, runtime as skill_runtime

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(staff_user)])


class SkillIn(BaseModel):
    name: str
    description: str | None = None
    instruction: str
    scope: str = "book"
    output_kind: str = "insight"
    default_tier: str = "tier_1"
    enabled: bool = True
    visibility: str = "private"
    shared_with: list[str] | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    scope: str | None = None
    output_kind: str | None = None
    default_tier: str | None = None
    enabled: bool | None = None
    visibility: str | None = None
    shared_with: list[str] | None = None


class RunIn(BaseModel):
    subject_type: str | None = None
    subject_id: uuid.UUID | None = None


def _visible(s: Skill, user: User) -> bool:
    return library.visible(s, user)


def _rec_dict(r: Recommendation) -> dict:
    return {"id": str(r.id), "run_id": str(r.run_id), "agent_key": r.agent_key, "tier": r.tier,
            "status": r.status, "title": r.title, "summary": r.summary, "rationale": r.rationale,
            "confidence": r.confidence, "priority": r.priority, "subject_label": r.subject_label,
            "subject_type": r.subject_type, "subject_id": str(r.subject_id) if r.subject_id else None,
            "payload": r.payload, "evidence": r.evidence, "citations": r.citations,
            "created_at": r.created_at.isoformat() if r.created_at else None}


def _fail(exc: library.SkillError) -> HTTPException:
    if isinstance(exc, library.SkillForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc))


async def _out(db, firm, user, s: Skill) -> dict:
    return library.as_dict(s, user, await library.owners(db, firm.id))


@router.get("")
async def list_skills(user: User = Depends(staff_user), firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    return await library.list_for(db, firm.id, user)


@router.get("/colleagues")
async def colleagues(user: User = Depends(staff_user), firm: Firm = Depends(current_firm),
                     db: AsyncSession = Depends(get_db)):
    """Staff colleagues a skill can be shared with."""
    return [{"id": str(u.id), "name": u.full_name, "role": u.role, "title": u.title}
            for u in await library.colleagues(db, firm.id, user)]


@router.post("")
async def create_skill(body: SkillIn, user: User = Depends(staff_user),
                       firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        return await _out(db, firm, user, await library.create(db, firm.id, user, body.model_dump(exclude_none=True)))
    except library.SkillError as exc:
        raise _fail(exc)


async def _get(db, firm, skill_id) -> Skill:
    try:
        return await library.get(db, firm.id, skill_id)
    except library.SkillError as exc:
        raise _fail(exc)


@router.patch("/{skill_id}")
async def update_skill(skill_id: uuid.UUID, body: SkillUpdate, user: User = Depends(staff_user),
                       firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        return await _out(db, firm, user, await library.update(db, firm.id, skill_id, user, body.model_dump(exclude_none=True)))
    except library.SkillError as exc:
        raise _fail(exc)


@router.delete("/{skill_id}")
async def delete_skill(skill_id: uuid.UUID, user: User = Depends(staff_user),
                       firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        await library.delete(db, firm.id, skill_id, user)
    except library.SkillError as exc:
        raise _fail(exc)
    return {"ok": True}


@router.post("/{skill_id}/clone")
async def clone_skill(skill_id: uuid.UUID, user: User = Depends(staff_user),
                      firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Copy a skill you can see into your own (private) workspace, to customise."""
    try:
        return await _out(db, firm, user, await library.clone(db, firm.id, skill_id, user))
    except library.SkillError as exc:
        raise _fail(exc)


@router.get("/{skill_id}/run-stream")
async def skill_run_stream(
    skill_id: uuid.UUID,
    test: bool = Query(False),
    subject_type: str | None = Query(None),
    subject_id: uuid.UUID | None = Query(None),
    user: User = Depends(staff_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """SSE stream for skill test/run — emits start, per-household scan/result, done events."""
    s = await _get(db, firm, skill_id)
    if not _visible(s, user):
        raise HTTPException(status_code=403, detail="You don't have access to this skill.")

    async def gen():
        try:
            async for ev in skill_runtime.stream_skill(
                db, firm, s,
                subject_type=subject_type,
                subject_id=subject_id,
                persist=not test,
            ):
                yield f"data: {json.dumps(ev)}\n\n"
            if test:
                await db.rollback()
        except Exception as exc:
            yield f"data: {json.dumps({'phase': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{skill_id}/test")
async def test_skill(skill_id: uuid.UUID, body: RunIn, user: User = Depends(staff_user),
                     firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    s = await _get(db, firm, skill_id)
    if not _visible(s, user):
        raise HTTPException(status_code=403, detail="You don't have access to this skill.")
    result = await skill_runtime.run_skill(db, firm, s, subject_type=body.subject_type,
                                           subject_id=body.subject_id, persist=False)
    await db.rollback()
    return result


@router.post("/{skill_id}/run")
async def run_skill_endpoint(skill_id: uuid.UUID, body: RunIn, user: User = Depends(staff_user),
                             firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    s = await _get(db, firm, skill_id)
    if not _visible(s, user):
        raise HTTPException(status_code=403, detail="You don't have access to this skill.")
    result = await skill_runtime.run_skill(db, firm, s, subject_type=body.subject_type,
                                           subject_id=body.subject_id, persist=True)
    recs = []
    if result["recommendation_ids"]:
        rows = (await db.execute(select(Recommendation).where(
            Recommendation.id.in_([uuid.UUID(r) for r in result["recommendation_ids"]])))).scalars().all()
        recs = [_rec_dict(r) for r in rows]
    return {**result, "recommendations": recs}
