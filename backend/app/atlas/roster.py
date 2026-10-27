"""The workforce roster - every agent as an on-duty colleague, for a firm.

One function, read by the Workforce page (`/api/atlas/workforce`) and by the conversation's
`read_agents` tool, so the page and the conversation cannot disagree about who is paused."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.core.db import utcnow
from app.models.governance import AgentActivity, AgentRun, Recommendation
from app.models.tenant import AgentConfig


async def workforce(db: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    configs = {
        c.agent_key: c for c in
        (await db.execute(select(AgentConfig).where(AgentConfig.firm_id == firm_id))).scalars().all()
    }
    # Latest activity per agent.
    acts = (await db.execute(
        select(AgentActivity).where(AgentActivity.firm_id == firm_id)
        .order_by(AgentActivity.created_at.desc()).limit(200))).scalars().all()
    last_by_agent: dict[str, AgentActivity] = {}
    for a in acts:
        last_by_agent.setdefault(a.agent_key, a)

    now = utcnow()
    out = []
    for key, meta in CATALOGUE.items():
        cfg = configs.get(key)
        runs = (await db.execute(
            select(func.count(AgentRun.id)).where(AgentRun.firm_id == firm_id, AgentRun.agent_key == key))).scalar_one()
        proposed = (await db.execute(
            select(func.count(Recommendation.id)).where(
                Recommendation.firm_id == firm_id, Recommendation.agent_key == key))).scalar_one()
        last = last_by_agent.get(key)
        stamp = last.created_at if last else None
        if stamp is not None and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=now.tzinfo)      # a naive timestamp is read as UTC
        recent = stamp is not None and (now - stamp) < timedelta(seconds=90)
        paused = cfg.paused if cfg else False
        status = "paused" if paused else ("working" if recent else "on-duty")
        out.append({
            "agent_key": key, "name": meta["name"], "stage": meta["stage"],
            "tier": (cfg.default_tier if cfg else meta["default_tier"]),
            "subject": meta.get("subject"), "checkpoint": meta.get("checkpoint"),
            "enabled": cfg.enabled if cfg else True, "paused": paused, "status": status,
            "paused_reason": cfg.paused_reason if cfg else None,
            "watching": meta["senses"], "acts": meta["acts"],
            "last_action": last.summary if last else "Standing by",
            "last_at": last.created_at.isoformat() if (last and last.created_at) else None,
            "runs": runs, "contributions": proposed,
        })
    return out
