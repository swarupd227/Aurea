"""A scheduled agent raises each open proposal once, and keeps it up to date.

    python -m tests.verify_proposals

The bug: every daily run of the stalled-onboarding agent raised the case again, so one case sat
in the queue sixty times and a reviewer had to clear all of them. Checked here through the real
run_agent path with a stand-in agent whose findings the test controls:

  the same finding on a later run updates the open proposal, it does not add a second
  a different subject, or a different kind of finding about the same subject, is its own proposal
  several proposals about one subject in a single run stay several
  a proposal someone has decided is never edited, and the next run raises a fresh one
  a full-book scan that stops finding a condition closes the proposal it raised for it
  an agent that is not scheduled, or opts out, still gets one proposal per run
  the ledger records each refresh and each expiry
  the backfill closes the duplicates already in the queue, and a second pass closes none
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> None:
    import app.models  # noqa: F401
    from app.atlas import proposals, runtime
    from app.atlas.base import BaseAgent, RecommendationDraft, Subject
    from app.core.db import Base
    from app.models.enums import AgentKey, AutonomyTier, HumanAction, RecommendationStatus
    from app.models.governance import LedgerEntry, Recommendation
    from app.models.tenant import Firm

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    case_a, case_b = uuid.uuid4(), uuid.uuid4()

    class Stand(BaseAgent):
        key = AgentKey.ABANDONMENT_RECOVERY
        name = "Stand-in"
        lifecycle_stage = "acquire_onboard"
        default_tier = AutonomyTier.TIER_1
        scheduled = True
        expires_unraised = True
        found: list[tuple[uuid.UUID, str, int]] = []   # (subject id, name, days idle)

        async def think(self, ctx, sensed):
            return [RecommendationDraft(
                title=f"Stalled onboarding — {name} ({days}d idle)", summary=f"Idle {days} days.",
                rationale=f"{name} has been idle {days} days.", priority=1,
                subject=Subject("onboarding_case", sid, name), payload={"idle_days": days},
                evidence={"idle_days": days}) for sid, name, days in self.found]

    agent = Stand()
    registry = {AgentKey.ABANDONMENT_RECOVERY: agent}
    runtime.get_agent = lambda key: registry.get(key)

    async def run(s, firm, **kw):
        r = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.ABANDONMENT_RECOVERY, **kw)
        await s.commit()
        return r

    async def count(s, firm, status=None, agent_key=AgentKey.ABANDONMENT_RECOVERY) -> int:
        q = select(func.count()).select_from(Recommendation).where(
            Recommendation.firm_id == firm.id, Recommendation.agent_key == agent_key)
        if status:
            q = q.where(Recommendation.status == status)
        return (await s.execute(q)).scalar_one()

    async def ledger_count(s, firm, event_type) -> int:
        return (await s.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == event_type))).scalar_one()

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="F", slug="f")
        s.add(firm)
        await s.commit()
        P = RecommendationStatus.PROPOSED

        print("\n=== the same finding on a later run updates the open proposal ===")
        agent.found = [(case_a, "Priya Raman", 70), (case_b, "Daniel Okonkwo", 59)]
        await run(s, firm)
        check("first run raises one per case", await count(s, firm, P), 2)
        first_ids = set((await s.execute(select(Recommendation.id).where(
            Recommendation.firm_id == firm.id))).scalars().all())
        for day in range(1, 6):
            agent.found = [(case_a, "Priya Raman", 70 + day), (case_b, "Daniel Okonkwo", 59 + day)]
            await run(s, firm)
        check("five more runs add nothing", await count(s, firm), 2)
        ids = set((await s.execute(select(Recommendation.id).where(
            Recommendation.firm_id == firm.id))).scalars().all())
        check("the same two rows, so their place in the queue is kept", ids, first_ids)
        rec = (await s.execute(select(Recommendation).where(
            Recommendation.subject_id == case_a))).scalar_one()
        check("the title carries the latest days idle", rec.title, "Stalled onboarding — Priya Raman (75d idle)")
        check("so does the payload", rec.payload["idle_days"], 75)
        check("it records how many times it was seen", rec.payload["refreshed"]["times"], 6)
        check("each refresh is in the ledger", await ledger_count(s, firm, "recommendation_refreshed"), 10)

        print("\n=== a refresh only run is not left waiting on a person ===")
        r = await run(s, firm)
        check("run completes", str(r.status), "completed")
        check("and says what it refreshed", len((r.context or {}).get("refreshed", [])), 2)

        print("\n=== a new subject is its own proposal ===")
        case_c = uuid.uuid4()
        agent.found = [(case_a, "Priya Raman", 77), (case_b, "Daniel Okonkwo", 66), (case_c, "Emma Whitfield", 4)]
        await run(s, firm)
        check("three open", await count(s, firm, P), 3)

        print("\n=== a decided proposal is never edited ===")
        rec_a = (await s.execute(select(Recommendation).where(
            Recommendation.subject_id == case_a))).scalar_one()
        await runtime.decide(s, firm=firm, recommendation=rec_a, action=HumanAction.DISMISS,
                             actor_id=None, actor_label="reviewer", note="chased by phone")
        await s.commit()
        title_before = rec_a.title
        agent.found = [(case_a, "Priya Raman", 78), (case_b, "Daniel Okonkwo", 67), (case_c, "Emma Whitfield", 5)]
        await run(s, firm)
        await s.refresh(rec_a)
        check("the dismissed one keeps its wording", rec_a.title, title_before)
        check("it stays dismissed", rec_a.status, RecommendationStatus.DISMISSED)
        check("a new one is raised for the still-stalled case", await count(s, firm, P), 3)

        print("\n=== a scan that stops finding a condition closes its proposal ===")
        agent.found = [(case_b, "Daniel Okonkwo", 68)]
        r = await run(s, firm)
        check("only the case still stalled is open", await count(s, firm, P), 1)
        check("the others are expired, not deleted",
              await count(s, firm, RecommendationStatus.EXPIRED), 2)
        check("the run says how many it closed", (r.context or {}).get("closed"), 2)
        check("each expiry is in the ledger", await ledger_count(s, firm, "recommendation_expired"), 2)

        print("\n=== a run for one subject does not close the rest ===")
        agent.found = []
        await run(s, firm, subject=Subject("onboarding_case", case_b, "Daniel Okonkwo"))
        check("still open", await count(s, firm, P), 1)

        print("\n=== several proposals about one subject in one run stay several ===")
        class Many(Stand):
            key = AgentKey.CLIENT_CARE
            expires_unraised = False
        many = Many()
        registry[AgentKey.CLIENT_CARE] = many
        hh = uuid.uuid4()
        many_drafts = lambda n: [  # noqa: E731
            RecommendationDraft(title=t, summary="s", subject=Subject("household", hh, "Chen"))
            for t in (f"Volatility outreach — Chen", f"Retirement milestone — Chen",
                      f"Engage next-gen heir — Chen {n}")]
        many.think = lambda ctx, sensed: _async(many_drafts(1))
        await runtime.run_agent(s, firm=firm, agent_key=AgentKey.CLIENT_CARE)
        await s.commit()
        many.think = lambda ctx, sensed: _async(many_drafts(2))
        await runtime.run_agent(s, firm=firm, agent_key=AgentKey.CLIENT_CARE)
        await s.commit()
        check("three kinds of finding, three proposals", await count(s, firm, P, AgentKey.CLIENT_CARE), 3)

        print("\n=== an agent that opts out raises one per run ===")
        class Billing(Stand):
            key = AgentKey.BILLING
            refreshes_open_proposals = False
            expires_unraised = False
        b = Billing()
        registry[AgentKey.BILLING] = b
        bhh = uuid.uuid4()
        b.think = lambda ctx, sensed: _async([RecommendationDraft(
            title="Billing run 2026-10-26 - 13 bill(s)", summary="s", subject=Subject("firm", bhh, "F"))])
        for _ in range(2):
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.BILLING)
            await s.commit()
        check("two runs, two proposals", await count(s, firm, P, AgentKey.BILLING), 2)

        print("\n=== an agent that is not scheduled raises one per run ===")
        class Manual(Stand):
            key = AgentKey.MEETING_PREP
            scheduled = False
            expires_unraised = False
        m = Manual()
        registry[AgentKey.MEETING_PREP] = m
        m.think = lambda ctx, sensed: _async([RecommendationDraft(
            title="Meeting brief — Q4 review", summary="s", subject=Subject("household", hh, "Chen"))])
        for _ in range(2):
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.MEETING_PREP)
            await s.commit()
        check("two runs, two proposals", await count(s, firm, P, AgentKey.MEETING_PREP), 2)

    print("\n=== the backfill closes duplicates already in the queue ===")
    async with Session() as s:
        firm2 = Firm(id=uuid.uuid4(), name="G", slug="g")
        s.add(firm2)
        await s.commit()
        agent.found = [(case_a, "Priya Raman", 60), (case_b, "Daniel Okonkwo", 50)]
        # Old behaviour: one run per day, each raising every case. Make the runs distinct
        # by running with dedupe off, then switch it on for the backfill.
        agent.refreshes_open_proposals = False
        agent.expires_unraised = False
        for day in range(8):
            agent.found = [(case_a, "Priya Raman", 60 + day), (case_b, "Daniel Okonkwo", 50 + day)]
            await run(s, firm2)
        agent.refreshes_open_proposals = True
        agent.expires_unraised = True
        P = RecommendationStatus.PROPOSED
        check("sixteen open before", await count(s, firm2, P), 16)
        report = await proposals.collapse_open_duplicates(s, firm_id=firm2.id)
        await s.commit()
        check("fourteen closed", report["expired"], 14)
        check("two stay open, one per case", await count(s, firm2, P), 2)
        newest = (await s.execute(select(Recommendation).where(
            Recommendation.firm_id == firm2.id, Recommendation.status == P))).scalars().all()
        check("and they are the newest", sorted(r.payload["idle_days"] for r in newest), [57, 67])
        again = await proposals.collapse_open_duplicates(s, firm_id=firm2.id)
        check("a second pass closes none", again["expired"], 0)
        check("each closure names what replaced it", all(
            "Superseded" in (r.decision_note or "") for r in (await s.execute(
                select(Recommendation).where(Recommendation.firm_id == firm2.id,
                                             Recommendation.status == RecommendationStatus.EXPIRED))
            ).scalars().all()), True)

    await engine.dispose()


async def _async(value):
    return value


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAIL else 0)
