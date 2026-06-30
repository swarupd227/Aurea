"""Streaming agent runs — emit the sense → think → act loop as events so the user can watch
the agent work (the signature agentic UX). Reuses the same governance as a normal run."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.atlas import activity, runtime
from app.atlas.base import AgentContext, Subject
from app.atlas.registry import get_agent
from app.core.db import utcnow
from app.models.enums import (
    ActivityKind, AgentKey, AgentRunStatus, HumanAction, MandateType, RecommendationStatus,
)
from app.models.governance import AgentRun, Recommendation
from app.models.tenant import Firm
from app.provenance import ledger, policy, surveillance

THINK_LABELS = {
    "drift_rebalancing": "Tax-lot selection · CGT budget · loss harvesting · concentration · ESG screens",
    "onboarding_kyc_aml": "Extracting documents and screening against the AML watchlist",
    "book_integration": "Reconciling clients, securities and holdings against the brain",
    "meeting_prep": "Assembling the brief from goals, positioning and life events",
    "meeting_companion": "Turning the transcript into notes, tasks and proposed goals",
    "research_reporting": "Drafting client-ready commentary grounded in the house views",
    "next_best_action": "Scanning for concentration, tax, life-stage and growth signals",
    "client_care": "Stress-testing the plan to ground the outreach",
    "conduct_surveillance": "Reviewing recommendations for suitability and fair conduct",
}


def _sense_summary(sensed: dict) -> str:
    if not sensed:
        return "Read the client brain"
    if "positions" in sensed:
        return f"Read {len(sensed['positions'])} positions, target model and tax lots — with lineage"
    if "documents" in sensed:
        return f"Extracted {len(sensed['documents'])} document(s) and screened the parties"
    if "brains" in sensed:
        return f"Read {len(sensed['brains'])} household(s) across the book"
    if "brain" in sensed:
        return "Read the household's holdings, goals and house views"
    if "mappings" in sensed or "stats" in sensed:
        return "Read the inbound book and matched it to the brain"
    return "Read the client brain"


async def stream_run(
    session: AsyncSession, *, firm: Firm, agent_key: AgentKey, subject: Subject,
    mandate_type: MandateType | None = None,
):
    """Async generator yielding event dicts across the agent's loop."""
    agent = get_agent(agent_key)
    name = runtime.agent_label(agent_key)
    if agent is None:
        yield {"phase": "error", "message": "Unknown agent"}
        return

    paused, reason = await policy.agent_paused(session, firm.id, agent_key)
    if paused:
        yield {"phase": "error", "message": reason or "Agent paused"}
        return

    tier, guardrails = await policy.resolve_tier(session, firm.id, agent_key, mandate_type)
    config = {}

    run = AgentRun(firm_id=firm.id, agent_key=agent_key, status=AgentRunStatus.SENSING, tier=tier,
                   trigger="stream", subject_type=subject.type, subject_id=subject.id,
                   subject_label=subject.label, started_at=utcnow(), context={})
    session.add(run)
    await session.flush()
    ctx = AgentContext(session=session, firm=firm, run=run, tier=tier, guardrails=guardrails,
                       config=config, subject=subject)

    yield {"phase": "start", "agent": name, "subject": subject.label, "tier": tier,
           "run_id": str(run.id)}
    await asyncio.sleep(0.15)

    # SENSE
    yield {"phase": "sense", "status": "start", "label": "Sensing — gathering signals from the brain"}
    sensed = await agent.sense(ctx)
    run.context = {"sensed": sensed}
    await activity.emit(session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.SENSING,
                        summary=f"{name} sensed the brain for {subject.label or 'the book'}",
                        subject_label=subject.label)
    await asyncio.sleep(0.5)
    yield {"phase": "sense", "status": "done", "detail": _sense_summary(sensed)}

    # THINK
    yield {"phase": "think", "status": "start",
           "label": THINK_LABELS.get(str(agent_key), "Reasoning over the client brain")}
    drafts = await agent.think(ctx, sensed)
    await asyncio.sleep(0.4)
    yield {"phase": "think", "status": "done"}

    if not drafts:
        run.status = AgentRunStatus.COMPLETED
        run.finished_at = utcnow()
        await activity.emit(session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.WATCHING,
                            summary=f"{name} checked {subject.label or 'the book'} — nothing to surface",
                            subject_label=subject.label)
        await session.commit()  # durable before we tell the client we're done
        yield {"phase": "empty", "summary": f"{name} checked — everything is within tolerance."}
        return

    d = drafts[0]
    # Stream the rationale token-by-token (the agent 'writing its reasoning').
    yield {"phase": "rationale", "status": "start"}
    words = (d.rationale or d.summary or "").split(" ")
    buf = []
    for w in words:
        buf.append(w)
        if len(buf) >= 4:
            yield {"phase": "rationale", "chunk": " ".join(buf) + " "}
            buf = []
            await asyncio.sleep(0.035)
    if buf:
        yield {"phase": "rationale", "chunk": " ".join(buf)}
    yield {"phase": "rationale", "status": "done"}

    # CHECK — suitability, conduct & pre-trade compliance review (its own visible stage).
    yield {"phase": "check", "status": "start",
           "label": "Suitability vs mandate · fair-conduct · pre-trade compliance"}

    # Persist every draft (same governance as a normal run).
    from app.core import foundation
    require_all = bool((await foundation.for_agent(session, firm, agent_key)).get("require_approval_everywhere"))
    rec_ids = []
    pending = False
    total_flags = 0
    for draft in drafts:
        rec = Recommendation(
            firm_id=firm.id, run_id=run.id, agent_key=agent_key, tier=tier,
            status=RecommendationStatus.PROPOSED, title=draft.title, summary=draft.summary,
            rationale=draft.rationale, confidence=draft.confidence, priority=draft.priority,
            subject_type=draft.subject.type or subject.type, subject_id=draft.subject.id or subject.id,
            subject_label=draft.subject.label or subject.label, payload=draft.payload,
            evidence=draft.evidence, citations=draft.citations)
        session.add(rec)
        await session.flush()
        rec_ids.append(str(rec.id))
        await ledger.append_entry(session, firm_id=firm.id, event_type="recommendation",
                                  agent_key=agent_key, run_id=run.id, recommendation_id=rec.id,
                                  actor="agent", content=runtime._ledger_content(rec, tier, "stream"))
        flags = await surveillance.review_recommendation(session, rec)
        total_flags += len(flags or [])
        if policy.requires_human_checkpoint(tier) or require_all:
            pending = True
            await activity.emit(session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.PROPOSED,
                                summary=f"{name}: {rec.title}", subject_label=rec.subject_label,
                                meta={"recommendation_id": str(rec.id)})
        else:
            await runtime._execute(session, agent, ctx, rec, actor="agent", action=HumanAction.APPROVE)
            await activity.emit(session, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.ACTED,
                                summary=f"{name} acted autonomously: {rec.title}",
                                subject_label=rec.subject_label, autonomous=True)

    await asyncio.sleep(0.35)
    from app.compliance.ontology import framework_for
    fw = framework_for(firm)
    yield {"phase": "check", "status": "done", "flags": total_flags,
           "detail": (f"{fw.regime} {fw.version}: {total_flags} flag(s) raised — surfaced to the adviser"
                      if total_flags else f"{fw.regime} {fw.version}: suitable, within mandate, no flags")}

    run.status = AgentRunStatus.AWAITING_APPROVAL if pending else AgentRunStatus.COMPLETED
    run.finished_at = None if pending else utcnow()
    await session.commit()  # durable before the client fetches the recommendation
    yield {"phase": "done", "run_id": str(run.id), "recommendation_id": rec_ids[0],
           "recommendation_ids": rec_ids, "title": d.title, "tier": tier,
           "pending": pending, "autonomous": not pending, "confidence": d.confidence}
