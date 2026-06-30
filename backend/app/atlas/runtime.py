"""Atlas runtime — orchestrates an agent run end-to-end with governance enforced.

sense → think → (HITL gate) → act, with a decision-ledger entry at every step, conduct
surveillance over every recommendation, autonomy-tier enforcement, and kill-switch checks."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.atlas import activity
from app.atlas.base import AgentContext, Subject
from app.atlas.registry import get_agent
from app.core.db import utcnow
from app.core.logging import get_logger
from app.models.enums import (
    ActivityKind,
    AgentKey,
    AgentRunStatus,
    AutonomyTier,
    HumanAction,
    MandateType,
    RecommendationStatus,
)


def agent_label(agent_key) -> str:
    meta = CATALOGUE.get(agent_key) or CATALOGUE.get(str(agent_key))
    return meta["name"] if meta else str(agent_key).replace("_", " ").title()
from app.models.governance import AgentRun, Recommendation
from app.models.tenant import AgentConfig, Firm
from app.provenance import ledger, policy, surveillance

log = get_logger("aurea.atlas")


class AgentPausedError(Exception):
    pass


async def run_agent(
    session: AsyncSession,
    *,
    firm: Firm,
    agent_key: AgentKey,
    subject: Subject | None = None,
    trigger: str = "manual",
    mandate_type: MandateType | None = None,
    overrides: dict | None = None,
) -> AgentRun:
    agent = get_agent(agent_key)
    if agent is None:
        raise ValueError(f"Unknown agent {agent_key}")

    paused, reason = await policy.agent_paused(session, firm.id, agent_key)
    if paused:
        raise AgentPausedError(reason or "Agent paused.")

    tier, guardrails = await policy.resolve_tier(session, firm.id, agent_key, mandate_type)
    subject = subject or Subject()

    cfg = (
        await session.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == firm.id, AgentConfig.agent_key == agent_key
            )
        )
    ).scalar_one_or_none()

    run = AgentRun(
        firm_id=firm.id,
        agent_key=agent_key,
        status=AgentRunStatus.SENSING,
        tier=tier,
        trigger=trigger,
        subject_type=subject.type,
        subject_id=subject.id,
        subject_label=subject.label,
        started_at=utcnow(),
        context={},
    )
    session.add(run)
    await session.flush()

    ctx = AgentContext(
        session=session, firm=firm, run=run, tier=tier, guardrails=guardrails,
        config={**(cfg.config if cfg else {}), "overrides": overrides or {}}, subject=subject,
    )

    try:
        run.status = AgentRunStatus.SENSING
        sensed = await agent.sense(ctx)
        run.context = {**(run.context or {}), "sensed": sensed}
        await activity.emit(
            session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.SENSING,
            summary=f"{agent_label(agent_key)} sensed the brain for {subject.label or 'the book'}",
            subject_label=subject.label,
        )

        run.status = AgentRunStatus.THINKING
        drafts = await agent.think(ctx, sensed)

        from app.core import foundation
        require_all = bool((await foundation.for_agent(session, firm, agent_key)).get("require_approval_everywhere"))
        pending = False
        for d in drafts:
            rec = Recommendation(
                firm_id=firm.id,
                run_id=run.id,
                agent_key=agent_key,
                tier=tier,
                status=RecommendationStatus.PROPOSED,
                title=d.title,
                summary=d.summary,
                rationale=d.rationale,
                confidence=d.confidence,
                priority=d.priority,
                subject_type=d.subject.type or subject.type,
                subject_id=d.subject.id or subject.id,
                subject_label=d.subject.label or subject.label,
                payload=d.payload,
                evidence=d.evidence,
                citations=d.citations,
            )
            session.add(rec)
            await session.flush()

            await ledger.append_entry(
                session, firm_id=firm.id, event_type="recommendation",
                agent_key=agent_key, run_id=run.id, recommendation_id=rec.id, actor="agent",
                content=_ledger_content(rec, tier, trigger),
            )
            await surveillance.review_recommendation(session, rec)

            if policy.requires_human_checkpoint(tier) or require_all:
                pending = True
                await activity.emit(
                    session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.PROPOSED,
                    summary=f"{agent_label(agent_key)}: {rec.title}", subject_label=rec.subject_label,
                    meta={"recommendation_id": str(rec.id), "tier": tier},
                )
            else:
                # Tier 3 — bounded autonomy: act immediately, post-hoc review.
                await _execute(session, agent, ctx, rec, actor="agent", action=HumanAction.APPROVE)
                await activity.emit(
                    session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.ACTED,
                    summary=f"{agent_label(agent_key)} acted autonomously: {rec.title}",
                    subject_label=rec.subject_label, autonomous=True,
                    meta={"recommendation_id": str(rec.id), "tier": tier},
                )

        if not drafts:
            await activity.emit(
                session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.WATCHING,
                summary=f"{agent_label(agent_key)} checked {subject.label or 'the book'} — nothing to surface",
                subject_label=subject.label,
            )

        run.status = AgentRunStatus.AWAITING_APPROVAL if pending else AgentRunStatus.COMPLETED
        run.finished_at = None if pending else utcnow()
    except Exception as exc:  # pragma: no cover
        run.status = AgentRunStatus.FAILED
        run.error = str(exc)
        run.finished_at = utcnow()
        log.exception("agent_run_failed", agent=agent_key, error=str(exc))
        raise
    await session.flush()
    log.info("agent_run", agent=agent_key, status=run.status, recs=len(drafts) if 'drafts' in dir() else 0)
    return run


def _ledger_content(rec: Recommendation, tier: AutonomyTier, trigger: str) -> dict:
    return {
        "trigger": trigger,
        "agent": rec.agent_key,
        "tier": tier,
        "subject": rec.subject_label,
        "recommendation": rec.title,
        "summary": rec.summary,
        "rationale": rec.rationale,
        "confidence": rec.confidence,
        "data_used": rec.evidence,
        "research_cited": rec.citations,
        "payload": rec.payload,
        "human_action": None,
    }


async def _execute(
    session: AsyncSession,
    agent,
    ctx: AgentContext,
    rec: Recommendation,
    *,
    actor: str,
    action: HumanAction,
) -> None:
    result = await agent.act(ctx, rec)
    rec.payload = {**(rec.payload or {}), "execution_result": result}
    if result.get("executed"):
        rec.status = RecommendationStatus.EXECUTED


async def decide(
    session: AsyncSession,
    *,
    firm: Firm,
    recommendation: Recommendation,
    action: HumanAction,
    actor_id: uuid.UUID | None,
    actor_label: str,
    note: str | None = None,
    modified_payload: dict | None = None,
) -> Recommendation:
    """Record a human decision at the HITL gate, act if approved, write to the ledger."""
    rec = recommendation
    rec.decided_by = actor_id
    rec.decided_at = utcnow()
    rec.decision_note = note

    if action == HumanAction.DISMISS:
        rec.status = RecommendationStatus.DISMISSED
    else:
        if action == HumanAction.MODIFY and modified_payload is not None:
            rec.modified_payload = modified_payload
            rec.status = RecommendationStatus.MODIFIED
        else:
            rec.status = RecommendationStatus.APPROVED

        # Advisor-defined skills use a non-enum agent key and have no executable action.
        try:
            ak = AgentKey(rec.agent_key)
            agent = get_agent(ak)
            tier, guardrails = await policy.resolve_tier(session, firm.id, ak, None)
        except ValueError:
            agent, tier, guardrails = None, rec.tier, {}
        run = await session.get(AgentRun, rec.run_id)
        ctx = AgentContext(
            session=session, firm=firm, run=run, tier=tier, guardrails=guardrails,
            config={}, subject=Subject(rec.subject_type, rec.subject_id, rec.subject_label),
        )
        if agent:
            await _execute(session, agent, ctx, rec, actor=actor_label, action=action)

    await ledger.append_entry(
        session, firm_id=firm.id, event_type="decision",
        agent_key=rec.agent_key, run_id=rec.run_id, recommendation_id=rec.id, actor=actor_label,
        content={
            "recommendation": rec.title,
            "human_action": action.value,
            "actor": actor_label,
            "note": note,
            "resulting_status": rec.status,
            "modified": modified_payload is not None,
        },
    )

    await activity.emit(
        session, firm_id=firm.id, agent_key=rec.agent_key, kind=ActivityKind.DECIDED,
        summary=f"{actor_label} {action.value}d: {rec.title}", subject_label=rec.subject_label,
        meta={"recommendation_id": str(rec.id), "action": action.value},
    )

    # Close the run if every recommendation has been decided.
    run = await session.get(AgentRun, rec.run_id)
    if run:
        siblings = (
            await session.execute(
                select(Recommendation).where(Recommendation.run_id == run.id)
            )
        ).scalars().all()
        if all(s.status != RecommendationStatus.PROPOSED for s in siblings):
            run.status = AgentRunStatus.COMPLETED
            run.finished_at = utcnow()

    await session.flush()
    log.info("decision", agent=rec.agent_key, action=action.value, status=rec.status)
    return rec


async def rollback(
    session: AsyncSession,
    *,
    firm: Firm,
    recommendation: Recommendation,
    actor_id: uuid.UUID | None,
    actor_label: str,
    note: str | None = None,
) -> Recommendation:
    """Reverse an approved/modified/executed recommendation and write a ledger entry."""
    rec = recommendation
    if rec.status not in (
        RecommendationStatus.APPROVED, RecommendationStatus.MODIFIED, RecommendationStatus.EXECUTED
    ):
        raise ValueError(f"Cannot roll back a {rec.status} recommendation.")

    try:
        agent = get_agent(AgentKey(rec.agent_key))
    except ValueError:
        agent = None
    run = await session.get(AgentRun, rec.run_id)
    ctx = AgentContext(
        session=session, firm=firm, run=run, tier=rec.tier, guardrails={}, config={},
        subject=Subject(rec.subject_type, rec.subject_id, rec.subject_label),
    )
    result = {"reversed": False, "note": "No agent."}
    if agent:
        result = await agent.rollback(ctx, rec)

    rec.status = RecommendationStatus.ROLLED_BACK
    rec.payload = {**(rec.payload or {}), "rollback_result": result}
    rec.decision_note = (note or rec.decision_note)
    await activity.emit(
        session, firm_id=firm.id, agent_key=rec.agent_key, kind=ActivityKind.ROLLED_BACK,
        summary=f"{actor_label} rolled back: {rec.title}", subject_label=rec.subject_label,
    )
    await ledger.append_entry(
        session, firm_id=firm.id, event_type="rollback", agent_key=rec.agent_key,
        run_id=rec.run_id, recommendation_id=rec.id, actor=actor_label,
        content={"recommendation": rec.title, "actor": actor_label, "note": note, "result": result},
    )
    await session.flush()
    log.info("rollback", agent=rec.agent_key, reversed=result.get("reversed"))
    return rec
