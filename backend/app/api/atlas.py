"""Atlas API — the agentic surface: live activity, streaming runs, delegation, workforce."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.api.deps import current_firm
from app.atlas import activity as activity_svc
from app.atlas import delegate as delegate_svc
from app.atlas import roster as roster_svc
from app.atlas import scan_stream as scan_stream_svc
from app.atlas import streaming
from app.atlas.subjects import SubjectNotFound, resolve_subject
from app.core import agent_runs
from app.core.db import get_db, utcnow
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.enums import AgentKey
from app.models.governance import AgentActivity, AgentRun, Recommendation
from app.models.identity import User
from app.models.tenant import AgentConfig, Firm

router = APIRouter(prefix="/api/atlas", tags=["atlas"], dependencies=[Depends(staff_user)])


# ── Live activity pulse ───────────────────────────────────────────────────────
@router.get("/activity")
async def activity(
    limit: int = 40, since: str | None = None,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            since_dt = None
    return await activity_svc.recent(db, firm.id, limit=limit, since=since_dt)


# ── Streaming run (sense → think → act, watch it work) ────────────────────────
@router.get("/run-stream")
async def run_stream(
    agent_key: AgentKey, subject_type: str | None = None, subject_id: uuid.UUID | None = None,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    try:
        subject, mandate_type = await resolve_subject(db, firm, subject_type, subject_id)
    except SubjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def gen():
        try:
            # Started by a person from the Cockpit, so held to a proposal like a conversation run.
            async for ev in streaming.stream_run(db, firm=firm, agent_key=agent_key,
                                                 subject=subject, mandate_type=mandate_type,
                                                 require_human=agent_runs.forced_to_proposal(agent_key)):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # pragma: no cover
            yield f"data: {json.dumps({'phase': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Streaming firm-wide book scan (watch it sweep the book) ────────────────────
@router.get("/scan-stream")
async def scan_stream(
    agent: str = "next_best_action",
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    key = AgentKey.CLIENT_CARE if agent == "client_care" else AgentKey.NEXT_BEST_ACTION

    async def gen():
        try:
            async for ev in scan_stream_svc.scan_book_stream(db, firm=firm, agent_key=key):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # pragma: no cover
            yield f"data: {json.dumps({'phase': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Delegate in natural language ──────────────────────────────────────────────
class DelegateIn(BaseModel):
    text: str


@router.post("/delegate")
async def delegate(
    body: DelegateIn, user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Interpret an instruction and return the routed task; the client then watches it run."""
    routing = await delegate_svc.interpret(db, firm.id, body.text)
    await activity_svc.emit(
        db, firm_id=firm.id, agent_key=str(routing["agent_key"]), kind="delegated",
        summary=f"{user.full_name} delegated: “{body.text.strip()}” → {routing['interpreted']}",
        subject_label=routing.get("subject_label"),
    )
    return {
        "agent_key": routing["agent_key"], "subject_type": routing["subject_type"],
        "subject_id": str(routing["subject_id"]) if routing.get("subject_id") else None,
        "subject_label": routing.get("subject_label"), "interpreted": routing["interpreted"],
    }


# ── Workforce roster (agents as on-duty colleagues) ───────────────────────────
@router.get("/workforce")
async def workforce(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await roster_svc.workforce(db, firm.id)