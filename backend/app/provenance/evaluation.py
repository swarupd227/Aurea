"""Evaluation harness + adaptive autonomy (spec §10.2, §13).

Agents are continuously scored against outcomes (how often their recommendations are approved,
modified, dismissed or rolled back, and how often surveillance flags them). A quality
regression automatically NARROWS an agent's autonomy — it never auto-widens (widening requires
governance sign-off). Every adaptive change is written to the decision ledger."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.core.logging import get_logger
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus, SurveillanceSeverity
from app.models.governance import (
    AgentEvaluation, AutonomyChange, Recommendation, SurveillanceFlag,
)
from app.models.tenant import AgentConfig
from app.provenance import ledger

log = get_logger("aurea.eval")

TIER_ORDER = [AutonomyTier.TIER_1, AutonomyTier.TIER_2, AutonomyTier.TIER_3]
MIN_SAMPLE = 4


async def compute_metrics(session: AsyncSession, firm_id: uuid.UUID, agent_key: str) -> dict:
    recs = (
        await session.execute(
            select(Recommendation).where(
                Recommendation.firm_id == firm_id, Recommendation.agent_key == agent_key
            )
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    conf_sum = 0.0
    for r in recs:
        counts[r.status] = counts.get(r.status, 0) + 1
        conf_sum += float(r.confidence or 0)

    decided = sum(counts.get(s, 0) for s in (
        RecommendationStatus.APPROVED, RecommendationStatus.MODIFIED, RecommendationStatus.DISMISSED,
        RecommendationStatus.EXECUTED, RecommendationStatus.ROLLED_BACK))
    approved = counts.get(RecommendationStatus.APPROVED, 0) + counts.get(RecommendationStatus.EXECUTED, 0) + counts.get(RecommendationStatus.MODIFIED, 0)
    dismissed = counts.get(RecommendationStatus.DISMISSED, 0)
    modified = counts.get(RecommendationStatus.MODIFIED, 0)
    rolled = counts.get(RecommendationStatus.ROLLED_BACK, 0)

    high_flags = (
        await session.execute(
            select(func.count(SurveillanceFlag.id)).where(
                SurveillanceFlag.firm_id == firm_id,
                SurveillanceFlag.target_agent_key == agent_key,
                SurveillanceFlag.severity == SurveillanceSeverity.HIGH,
            )
        )
    ).scalar_one()

    d = max(decided, 1)
    n = max(len(recs), 1)
    dismiss_rate = dismissed / d
    rollback_rate = rolled / d
    modify_rate = modified / d
    high_flag_rate = high_flags / n
    approval_rate = approved / d

    score = 1.0 - dismiss_rate * 0.5 - rollback_rate * 0.6 - high_flag_rate * 0.7 - modify_rate * 0.1
    score = round(max(0.0, min(1.0, score)), 3)

    if decided < 3:
        grade = "unrated"
    elif score >= 0.75:
        grade = "healthy"
    elif score >= 0.5:
        grade = "watch"
    else:
        grade = "regressed"

    return {
        "quality_score": score, "grade": grade, "sample_size": decided,
        "metrics": {
            "total": len(recs), "approval_rate": round(approval_rate, 3),
            "dismiss_rate": round(dismiss_rate, 3), "modify_rate": round(modify_rate, 3),
            "rollback_rate": round(rollback_rate, 3), "high_flags": high_flags,
            "avg_confidence": round(conf_sum / n, 3), "by_status": counts,
        },
    }


async def _narrow(session, firm_id, cfg: AgentConfig, reason: str) -> AutonomyChange | None:
    """Narrow one tier, or pause if already at Tier 1. Never widens."""
    current = cfg.default_tier
    idx = TIER_ORDER.index(current) if current in TIER_ORDER else 0
    change = AutonomyChange(firm_id=firm_id, agent_key=cfg.agent_key, from_tier=current,
                            automatic=True, reason=reason)
    if idx > 0:
        cfg.default_tier = TIER_ORDER[idx - 1]
        change.to_tier = cfg.default_tier
    else:
        cfg.paused = True
        cfg.paused_reason = f"Auto-paused by evaluation harness: {reason}"
        change.to_tier = current
        change.paused = True
    session.add(change)
    await session.flush()
    await ledger.append_entry(
        session, firm_id=firm_id, event_type="autonomy_change", agent_key=cfg.agent_key,
        actor="evaluation_harness",
        content={"from_tier": change.from_tier, "to_tier": change.to_tier,
                 "paused": change.paused, "automatic": True, "reason": reason},
    )
    log.warning("autonomy_narrowed", agent=cfg.agent_key, to=change.to_tier, paused=change.paused)
    return change


async def evaluate_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Score every agent, persist evaluations, and apply adaptive autonomy on regressions."""
    results = []
    changes = 0
    for agent_key in CATALOGUE:
        m = await compute_metrics(session, firm_id, agent_key)
        session.add(AgentEvaluation(
            firm_id=firm_id, agent_key=agent_key, quality_score=m["quality_score"],
            grade=m["grade"], metrics=m["metrics"], sample_size=m["sample_size"]))

        if m["grade"] == "regressed" and m["sample_size"] >= MIN_SAMPLE:
            cfg = (
                await session.execute(
                    select(AgentConfig).where(
                        AgentConfig.firm_id == firm_id, AgentConfig.agent_key == agent_key)
                )
            ).scalar_one_or_none()
            if cfg and not cfg.paused:
                await _narrow(session, firm_id, cfg,
                              f"quality score {m['quality_score']:.2f} (dismiss "
                              f"{m['metrics']['dismiss_rate']:.0%}, rollback {m['metrics']['rollback_rate']:.0%}, "
                              f"{m['metrics']['high_flags']} high flag(s))")
                changes += 1
        results.append({"agent_key": agent_key, **m})

    await session.flush()
    return {"evaluated": len(results), "autonomy_changes": changes, "agents": results}


async def latest_evaluations(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    rows = (
        await session.execute(
            select(AgentEvaluation).where(AgentEvaluation.firm_id == firm_id)
            .order_by(AgentEvaluation.created_at.desc())
        )
    ).scalars().all()
    seen: dict[str, dict] = {}
    for r in rows:
        if r.agent_key in seen:
            continue
        seen[r.agent_key] = {
            "agent_key": r.agent_key, "quality_score": r.quality_score, "grade": r.grade,
            "sample_size": r.sample_size, "metrics": r.metrics,
            "computed_at": r.created_at.isoformat() if r.created_at else None,
        }
    return list(seen.values())
