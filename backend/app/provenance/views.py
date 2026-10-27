"""The reads the Provenance page makes, as functions: the decision ledger, surveillance flags and
autonomy changes. The routes in api/provenance.py and the conversation's `read_provenance` tool
both call these, so the page and the conversation show the same record."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import AutonomyChange, LedgerEntry, SurveillanceFlag


async def recent_ledger(db: AsyncSession, firm_id: uuid.UUID, limit: int = 100) -> list[dict]:
    entries = (
        await db.execute(
            select(LedgerEntry).where(LedgerEntry.firm_id == firm_id)
            .order_by(LedgerEntry.seq.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "seq": e.seq, "event_type": e.event_type, "agent_key": e.agent_key,
            "actor": e.actor, "entry_hash": e.entry_hash, "prev_hash": e.prev_hash,
            "content": e.content, "created_at": e.created_at.isoformat() if e.created_at else None,
            "recommendation_id": str(e.recommendation_id) if e.recommendation_id else None,
        }
        for e in entries
    ]


async def autonomy_changes(db: AsyncSession, firm_id: uuid.UUID, limit: int = 50) -> list[dict]:
    rows = (
        await db.execute(
            select(AutonomyChange).where(AutonomyChange.firm_id == firm_id)
            .order_by(AutonomyChange.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {"agent_key": c.agent_key, "from_tier": c.from_tier, "to_tier": c.to_tier,
         "paused": c.paused, "automatic": c.automatic, "reason": c.reason,
         "created_at": c.created_at.isoformat() if c.created_at else None}
        for c in rows
    ]


async def surveillance_flags(db: AsyncSession, firm_id: uuid.UUID, limit: int = 200) -> list[dict]:
    flags = (
        await db.execute(
            select(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm_id)
            .order_by(SurveillanceFlag.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(f.id), "severity": f.severity, "category": f.category, "finding": f.finding,
            "target_agent_key": f.target_agent_key, "resolved": f.resolved,
            "auto_paused_agent": f.auto_paused_agent,
            "recommendation_id": str(f.recommendation_id) if f.recommendation_id else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "resolution_note": f.resolution_note, "resolved_by": f.resolved_by,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "escalated": f.escalated, "escalated_to": f.escalated_to,
            "escalated_at": f.escalated_at.isoformat() if f.escalated_at else None,
            "escalation_note": f.escalation_note,
        }
        for f in flags
    ]
