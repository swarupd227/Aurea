"""The workforce activity 'pulse' — append-only display events the cockpit streams live."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ActivityKind
from app.models.governance import AgentActivity


async def emit(
    session: AsyncSession,
    *,
    firm_id: uuid.UUID,
    agent_key: str,
    kind: ActivityKind | str,
    summary: str,
    subject_label: str | None = None,
    autonomous: bool = False,
    meta: dict | None = None,
) -> AgentActivity:
    ev = AgentActivity(
        firm_id=firm_id, agent_key=str(agent_key), kind=str(kind), summary=summary,
        subject_label=subject_label, autonomous=autonomous, meta=meta or {},
    )
    session.add(ev)
    await session.flush()
    return ev


def _to_dict(e: AgentActivity) -> dict:
    return {
        "id": str(e.id), "agent_key": e.agent_key, "kind": e.kind, "summary": e.summary,
        "subject_label": e.subject_label, "autonomous": e.autonomous,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


async def recent(
    session: AsyncSession, firm_id: uuid.UUID, *, limit: int = 40, since: datetime | None = None
) -> list[dict]:
    stmt = select(AgentActivity).where(AgentActivity.firm_id == firm_id)
    if since is not None:
        stmt = stmt.where(AgentActivity.created_at > since)
    rows = (await session.execute(stmt.order_by(AgentActivity.created_at.desc()).limit(limit))).scalars().all()
    return [_to_dict(e) for e in rows]
