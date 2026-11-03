"""Agent run history and capacity outcomes - the two reads the Agent history and Capacity pages
make, as functions, so the pages' routes and the conversation's tools read the same figures."""
from __future__ import annotations

import uuid
from datetime import date as _date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RecommendationStatus
from app.models.governance import AgentRun, Recommendation

# Indicative time saved: ~25 min of preparation per proposal a person decided. This is an assumption,
# not a measurement, and is returned with the figure so no surface shows it bare. A run counts for
# nothing on its own: a scheduled scan that found nothing saved nobody 25 minutes.
MINUTES_PER_ITEM = 25


def hours_reclaimed(decided: int) -> float:
    return round(decided * MINUTES_PER_ITEM / 60, 1)


async def run_history(
    db: AsyncSession, firm_id: uuid.UUID, *, agent_key: str | None = None, status: str | None = None,
    date_from: str | None = None, date_to: str | None = None, limit: int = 50,
) -> list[dict]:
    """Historical AgentRun records for a firm, newest first, with optional filters."""
    q = select(AgentRun).where(AgentRun.firm_id == firm_id)
    if agent_key:
        q = q.where(AgentRun.agent_key == agent_key)
    if status:
        q = q.where(AgentRun.status == status)
    if date_from:
        try:
            q = q.where(AgentRun.created_at >= _date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.where(AgentRun.created_at <= _date.fromisoformat(date_to))
        except ValueError:
            pass
    q = q.order_by(AgentRun.created_at.desc()).limit(min(limit, 200))
    runs = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id),
            "agent_key": r.agent_key,
            "status": r.status,
            "tier": r.tier,
            "trigger": r.trigger,
            "duration_ms": r.duration_ms if hasattr(r, "duration_ms") else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in runs
    ]


async def capacity_outcomes(db: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Capacity & outcomes - how AI-reclaimed time is translating into client outcomes."""
    total_runs = (
        await db.execute(select(func.count(AgentRun.id)).where(AgentRun.firm_id == firm_id))
    ).scalar_one()
    by_status = {}
    for st in RecommendationStatus:
        n = (
            await db.execute(
                select(func.count(Recommendation.id)).where(
                    Recommendation.firm_id == firm_id, Recommendation.status == st
                )
            )
        ).scalar_one()
        by_status[st.value] = n
    decided = by_status["approved"] + by_status["modified"] + by_status["dismissed"] + by_status["executed"]
    return {
        "total_agent_runs": total_runs,
        "recommendations_by_status": by_status,
        "open_items": by_status["proposed"],
        "decisions_made": decided,
        "estimated_hours_reclaimed": hours_reclaimed(decided),
    }
