"""Layer 2.6 — Agent adoption & ROI analytics.

Answers: which agents are in use, by whom, at what approval rate, and what productivity
impact (hours saved, cost displaced) does AI-assisted advice deliver?"""
from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.models.identity import User

# Analyst / compliance time assumptions (minutes per action displaced)
_MINS_PER_DECISION = 25  # time a human analyst would spend reviewing one recommendation
_MINS_PER_RUN = 15       # monitoring / data-pull time displaced per agent run
_ANALYST_RATE_USD_H = 75


async def compute(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    # ── 1. Per-agent run volume ────────────────────────────────────────────────
    run_rows = (await session.execute(
        select(AgentRun.agent_key, func.count(AgentRun.id).label("runs"))
        .where(AgentRun.firm_id == firm_id)
        .group_by(AgentRun.agent_key)
    )).all()
    runs_by_agent: dict[str, int] = {r.agent_key: r.runs for r in run_rows}

    # ── 2. Per-agent recommendation decision outcome stats ─────────────────────
    dec_rows = (await session.execute(
        select(
            Recommendation.agent_key,
            func.count(Recommendation.id).label("total"),
            func.sum(case((Recommendation.status == RecommendationStatus.APPROVED, 1), else_=0)).label("approved"),
            func.sum(case((Recommendation.status == RecommendationStatus.DISMISSED, 1), else_=0)).label("dismissed"),
            func.sum(case((Recommendation.status == RecommendationStatus.MODIFIED, 1), else_=0)).label("modified"),
        )
        .where(Recommendation.firm_id == firm_id, Recommendation.decided_by.isnot(None))
        .group_by(Recommendation.agent_key)
    )).all()
    dec_by_agent = {r.agent_key: r for r in dec_rows}

    # ── 3. Unique advisers using each agent (feature uptake) ──────────────────
    uptake_rows = (await session.execute(
        select(
            Recommendation.agent_key,
            func.count(func.distinct(Recommendation.decided_by)).label("advisers_using"),
        )
        .where(Recommendation.firm_id == firm_id, Recommendation.decided_by.isnot(None))
        .group_by(Recommendation.agent_key)
    )).all()
    uptake_by_agent: dict[str, int] = {r.agent_key: r.advisers_using for r in uptake_rows}

    # ── 4. Total unique advisers who have ever reviewed a recommendation ───────
    total_advisers: int = (await session.execute(
        select(func.count(func.distinct(Recommendation.decided_by)))
        .where(Recommendation.firm_id == firm_id, Recommendation.decided_by.isnot(None))
    )).scalar_one() or 0

    # ── 5. Per-adviser engagement ─────────────────────────────────────────────
    adv_rows = (await session.execute(
        select(
            User.id, User.full_name, User.email,
            func.count(Recommendation.id).label("decisions"),
            func.sum(case((Recommendation.status == RecommendationStatus.APPROVED, 1), else_=0)).label("approved"),
            func.sum(case((Recommendation.status == RecommendationStatus.DISMISSED, 1), else_=0)).label("dismissed"),
            func.sum(case((Recommendation.status == RecommendationStatus.MODIFIED, 1), else_=0)).label("modified"),
            func.count(func.distinct(Recommendation.agent_key)).label("agents_used"),
        )
        .join(User, Recommendation.decided_by == User.id)
        .where(Recommendation.firm_id == firm_id, Recommendation.decided_by.isnot(None))
        .group_by(User.id, User.full_name, User.email)
        .order_by(func.count(Recommendation.id).desc())
    )).all()

    # ── Build per-agent list ──────────────────────────────────────────────────
    all_keys = sorted(set(runs_by_agent) | set(dec_by_agent))
    by_agent = []
    for key in all_keys:
        d = dec_by_agent.get(key)
        total = d.total if d else 0
        approved = int(d.approved or 0) if d else 0
        dismissed = int(d.dismissed or 0) if d else 0
        modified = int(d.modified or 0) if d else 0
        advisers_using = uptake_by_agent.get(key, 0)
        by_agent.append({
            "agent_key": key,
            "runs": runs_by_agent.get(key, 0),
            "decisions": total,
            "approved": approved,
            "dismissed": dismissed,
            "modified": modified,
            "approval_rate": round(approved / total, 3) if total else None,
            "advisers_using": advisers_using,
            "uptake_pct": round(advisers_using / total_advisers, 3) if total_advisers else 0.0,
        })
    by_agent.sort(key=lambda x: -x["runs"])

    # ── Build per-adviser list ────────────────────────────────────────────────
    by_adviser = []
    for r in adv_rows:
        total_d = r.decisions or 0
        approved_d = int(r.approved or 0)
        by_adviser.append({
            "name": r.full_name or r.email,
            "email": r.email,
            "decisions": total_d,
            "approved": approved_d,
            "dismissed": int(r.dismissed or 0),
            "modified": int(r.modified or 0),
            "approval_rate": round(approved_d / total_d, 3) if total_d else None,
            "agents_used": r.agents_used or 0,
        })

    # ── Summary & ROI ─────────────────────────────────────────────────────────
    total_decisions = sum(r.decisions for r in adv_rows)
    total_runs_count = sum(runs_by_agent.values())
    hours_saved = round((total_decisions * _MINS_PER_DECISION + total_runs_count * _MINS_PER_RUN) / 60, 1)
    cost_displaced = round(hours_saved * _ANALYST_RATE_USD_H)

    return {
        "summary": {
            "total_advisers_active": total_advisers,
            "total_agent_runs": total_runs_count,
            "total_decisions": total_decisions,
            "hours_saved": hours_saved,
            "cost_displaced_usd": cost_displaced,
        },
        "by_agent": by_agent,
        "by_adviser": by_adviser,
    }
