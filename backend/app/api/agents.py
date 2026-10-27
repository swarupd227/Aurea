"""Agents API — catalogue, trigger runs, inspect runs."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.api.deps import current_firm
from app.atlas.registry import all_agents
from app.atlas.runtime import AgentPausedError, run_agent
from app.atlas.subjects import SubjectNotFound, resolve_subject
from app.core import agent_runs
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.enums import AgentKey
from app.models.governance import AgentRun, Recommendation
from app.models.tenant import AgentConfig, Firm

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(staff_user)])


class RunRequest(BaseModel):
    subject_type: str | None = None  # mandate | household | person | firm
    subject_id: uuid.UUID | None = None


def _rec_dict(r: Recommendation) -> dict:
    return {
        "id": str(r.id), "run_id": str(r.run_id), "agent_key": r.agent_key, "tier": r.tier,
        "status": r.status, "title": r.title, "summary": r.summary, "rationale": r.rationale,
        "confidence": r.confidence, "priority": r.priority, "subject_label": r.subject_label,
        "subject_type": r.subject_type, "subject_id": str(r.subject_id) if r.subject_id else None,
        "payload": r.modified_payload or r.payload, "evidence": r.evidence, "citations": r.citations,
        "decision_note": r.decision_note,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _run_dict(run: AgentRun, recs: list[Recommendation]) -> dict:
    return {
        "id": str(run.id), "agent_key": run.agent_key, "status": run.status, "tier": run.tier,
        "trigger": run.trigger, "subject_label": run.subject_label, "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "recommendations": [_rec_dict(r) for r in recs],
    }


@router.get("/catalogue")
async def catalogue(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    configs = {
        c.agent_key: c
        for c in (
            await db.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))
        ).scalars().all()
    }
    out = []
    for key, meta in CATALOGUE.items():
        cfg = configs.get(key)
        out.append({
            "agent_key": key, **meta,
            "enabled": cfg.enabled if cfg else True,
            "paused": cfg.paused if cfg else False,
            "paused_reason": cfg.paused_reason if cfg else None,
            "tier": (cfg.default_tier if cfg else meta["default_tier"]),
        })
    return out


@router.post("/{agent_key}/run")
async def trigger_run(
    agent_key: AgentKey, body: RunRequest,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    if agent_key not in all_agents():
        raise HTTPException(status_code=404, detail="Unknown agent")

    try:
        subject, mandate_type = await resolve_subject(db, firm, body.subject_type, body.subject_id)
    except SubjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        run = await run_agent(
            db, firm=firm, agent_key=agent_key, subject=subject,
            trigger="manual", mandate_type=mandate_type,
            require_human=agent_runs.forced_to_proposal(agent_key),
        )
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    recs = (
        await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))
    ).scalars().all()
    return _run_dict(run, recs)


@router.get("/runs")
async def list_runs(
    agent_key: AgentKey | None = None, limit: int = 50,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    stmt = select(AgentRun).where(AgentRun.firm_id == firm.id)
    if agent_key:
        stmt = stmt.where(AgentRun.agent_key == agent_key)
    runs = (await db.execute(stmt.order_by(AgentRun.created_at.desc()).limit(limit))).scalars().all()
    return [
        {"id": str(r.id), "agent_key": r.agent_key, "status": r.status, "tier": r.tier,
         "trigger": r.trigger, "subject_label": r.subject_label,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in runs
    ]


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    run = await db.get(AgentRun, run_id)
    if not run or run.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Run not found")
    recs = (
        await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))
    ).scalars().all()
    return {**_run_dict(run, recs), "context": run.context}
