"""Seed exception / non-happy-path scenarios so the governance spine visibly triggers.

Creates three flagged situations and runs them through Conduct Surveillance, so the demo shows the
platform saying "no": an over-promising client draft (fair-treatment), a low-confidence recommendation
(data quality), and a book-integration breach that AUTO-PAUSES that agent (the supervisor kill-switch).
Idempotent (skips if already seeded). Run: `python -m seed.exceptions`; also called from run.py.

Note: this intentionally leaves the **book_integration** agent paused. It is a secondary agent (not used
in the core cockpit/drift/analytics demo) and is resettable from Admin → Agents (set paused off)."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.models.enums import AgentKey, AgentRunStatus, AutonomyTier, RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Household
from app.models.tenant import AgentConfig, Firm
from app.provenance import surveillance


async def _ensure_config(s, firm_id, agent_key: str) -> AgentConfig:
    cfg = (await s.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm_id, AgentConfig.agent_key == agent_key)
    )).scalar_one_or_none()
    if not cfg:
        cfg = AgentConfig(firm_id=firm_id, agent_key=agent_key)
        s.add(cfg)
        await s.flush()
    return cfg


async def _make_rec(s, firm, *, agent_key, tier, title, summary, rationale, confidence,
                    subject_id, subject_label, subject_type="household", payload=None, evidence=None):
    run = AgentRun(firm_id=firm.id, agent_key=agent_key, status=AgentRunStatus.AWAITING_APPROVAL,
                   tier=tier, trigger="seed_exception", subject_type=subject_type, subject_id=subject_id,
                   subject_label=subject_label, started_at=utcnow(), context={})
    s.add(run)
    await s.flush()
    rec = Recommendation(firm_id=firm.id, run_id=run.id, agent_key=agent_key, tier=tier,
                         status=RecommendationStatus.PROPOSED, title=title, summary=summary,
                         rationale=rationale, confidence=confidence, priority=2,
                         subject_type=subject_type, subject_id=subject_id, subject_label=subject_label,
                         payload=payload or {}, evidence=evidence or {}, citations=[])
    s.add(rec)
    await s.flush()
    return rec


async def seed_exceptions(s, firm) -> int:
    # Idempotency — skip if we've already seeded these.
    if (await s.execute(
        select(AgentRun).where(AgentRun.firm_id == firm.id, AgentRun.trigger == "seed_exception")
    )).scalars().first():
        return 0

    households = (await s.execute(
        select(Household).where(Household.firm_id == firm.id).order_by(Household.created_at)
    )).scalars().all()
    if len(households) < 2:
        return 0
    h1, h2 = households[1 % len(households)], households[2 % len(households)]
    created = 0

    # 1. Over-promising client communication → fair-treatment flag (MEDIUM).
    await _ensure_config(s, firm.id, AgentKey.RESEARCH_REPORTING.value)
    rec1 = await _make_rec(
        s, firm, agent_key=AgentKey.RESEARCH_REPORTING.value, tier=AutonomyTier.TIER_1,
        title=f"Client commentary draft — {h1.name}",
        summary="Client-facing draft — held for review before sending.",
        rationale=("These holdings offer guaranteed, risk-free returns and will definitely outperform "
                   "the market this year — you can't lose."),
        confidence=0.82, subject_id=h1.id, subject_label=h1.name)
    await surveillance.review_recommendation(s, rec1)
    created += 1

    # 2. Low data confidence feeding a regulated decision → data-quality flag (MEDIUM).
    await _ensure_config(s, firm.id, AgentKey.NEXT_BEST_ACTION.value)
    rec2 = await _make_rec(
        s, firm, agent_key=AgentKey.NEXT_BEST_ACTION.value, tier=AutonomyTier.TIER_1,
        title=f"Possible concentration — {h2.name}",
        summary="Surfaced, but built on low-confidence data.",
        rationale=("Holding data for this account is stale and partially unreconciled across custodians; "
                   "verify the underlying positions before acting."),
        confidence=0.44, subject_id=h2.id, subject_label=h2.name,
        evidence={"source": "Client-brain scan", "scope": "household", "data_confidence": 0.44})
    await surveillance.review_recommendation(s, rec2)
    created += 1

    # 3. Book-integration breach → HIGH suitability flag → AUTO-PAUSE (supervisor kill-switch).
    await _ensure_config(s, firm.id, AgentKey.BOOK_INTEGRATION.value)
    rec3 = await _make_rec(
        s, firm, agent_key=AgentKey.BOOK_INTEGRATION.value, tier=AutonomyTier.TIER_2,
        title="Reconcile acquired book — unmatched holdings",
        summary="Inbound holdings exceed the reconciliation tolerance.",
        rationale=("The inbound acquired book contains holdings that cannot be matched to the golden "
                   "record within tolerance — manual review required before any commit."),
        confidence=0.6, subject_type="firm", subject_id=firm.id, subject_label="Acquired book — Sokolov",
        evidence={"guardrail_breaches": [
            "3 inbound holdings cannot be reconciled to the brain within tolerance — manual review required."]})
    await surveillance.review_recommendation(s, rec3)  # HIGH → auto-pauses book_integration
    created += 1

    await s.flush()
    return created


async def _main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one()
        n = await seed_exceptions(s, firm)
        await s.commit()
        print(f"exceptions: created {n} scenario(s)")


if __name__ == "__main__":
    asyncio.run(_main())
