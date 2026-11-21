"""Agents learn from the decisions people take, in the open (B-204).

    python -m tests.verify_decision_memory

Against a throwaway SQLite database, through the real run path. Checks that:

  - a preference is made only from a proposal the person may decide, in their own words, a hold with an end date
    within a year, and its Confirm card says what it is and that it never widens autonomy;
  - a control's finding (supervision, IPS, reconciliation, billing, screening) is never held, only noted;
  - a run keeps back what a hold covers and says so in its context, leaves that subject's open proposal open rather
    than closing it as resolved, and raises the rest with the last decision and its reason on them;
  - ending the hold raises the finding again; the maker or a decider may end it, nobody else;
  - a client holds none of these tools.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.atlas.base import BaseAgent, RecommendationDraft, Subject
from app.aurea_core import decision_memory
from app.core.db import Base, utcnow
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.identity import AuditEvent, User
from tests import _sim_fixture

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, decision_memory.PreferenceError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rows_of(preview) -> dict:
    return {r["label"]: r["value"] for r in preview["rows"]}


class FakeTax(BaseAgent):
    """Stands in for the tax agent's think step: one finding per household, the same title each run."""
    key = AgentKey.TAX_INTELLIGENCE
    name = "Tax Intelligence"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    expires_unraised = True
    households: list = []

    async def sense(self, ctx):
        return {}

    async def think(self, ctx, sensed):
        return [RecommendationDraft(title=f"Harvest the AAPL loss — {h.name}", summary="A loss to harvest.", rationale="x",
                                    confidence=0.8, priority=2, subject=Subject("household", h.id, h.name), payload={},
                                    evidence={}) for h in self.households]

    async def act(self, ctx, rec):
        return {"executed": True}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            fx = await _sim_fixture.build(s)
            firm, sophie = fx.firm, fx.adviser
            chen = fx.households["The Chen Family"]["household"]
            nguyen = fx.households["The Nguyen Family"]["household"]
            client = User(id=uuid.uuid4(), firm_id=firm.id, email="c@x", full_name="Wei", role=UserRole.CLIENT, hashed_password="x")
            s.add(client)
            run = AgentRun(firm_id=firm.id, agent_key=AgentKey.TAX_INTELLIGENCE, status="completed")
            s.add(run)
            await s.flush()
            now = utcnow()

            def rec(h, status, note=None, agent=AgentKey.TAX_INTELLIGENCE, title=None, days=10):
                r = Recommendation(firm_id=firm.id, run_id=run.id, agent_key=agent, tier="tier_1", status=status,
                                   title=title or f"Harvest the AAPL loss — {h.name}", summary="s", rationale="r",
                                   subject_type="household", subject_id=h.id, subject_label=h.name,
                                   decided_by=sophie.id if status != RecommendationStatus.PROPOSED else None,
                                   decided_at=now - timedelta(days=days) if status != RecommendationStatus.PROPOSED else None,
                                   decision_note=note)
                s.add(r)
                return r
            dismissed = rec(chen, RecommendationStatus.DISMISSED, "Wei wants to hold AAPL through the year")
            approved_n = rec(nguyen, RecommendationStatus.APPROVED, "Agreed at the review")
            ips_rec = rec(chen, RecommendationStatus.DISMISSED, "Accepted", agent=AgentKey.IPS_MONITORING, title="Equity over its IPS limit")
            open_chen = rec(chen, RecommendationStatus.PROPOSED, days=0)
            await s.flush()
            ex = lambda u: ToolExecutors(s, u.id, u.role, firm.id)  # noqa: E731
            today = date.today()

            print("\n=== Making a preference ===")
            base = {"recommendation_id": str(dismissed.id), "kind": "hold", "text": "Wei holds AAPL this year", "until": (today + timedelta(days=90)).isoformat()}
            await refused("a hold needs its end date", ex(sophie).preview_change("remember_preference", {**base, "until": None}), "date it ends")
            await refused("a hold lasts a year at most", ex(sophie).preview_change("remember_preference", {**base, "until": (today + timedelta(days=400)).isoformat()}), "a year at most")
            await refused("in the person's own words", ex(sophie).preview_change("remember_preference", {**base, "text": "x"}), "own words")
            await refused("hold or note only", ex(sophie).preview_change("remember_preference", {**base, "kind": "forever"}), "hold")
            await refused("a control's finding is never held", ex(sophie).preview_change("remember_preference", {**base, "recommendation_id": str(ips_rec.id)}), "control")
            card = rows_of(await ex(sophie).preview_change("remember_preference", base))
            check("the Confirm card names the agent, the subject, the hold, the words and what it never does",
                  (card["Agent"], card["Subject"], card["Kind"], card["In your words"], card["Never"]),
                  ("tax_intelligence", "The Chen Family", "Hold: not raised again", "Wei holds AAPL this year",
                   "Widens the agent's autonomy or changes a fact"))
            made = await ex(sophie).execute("remember_preference", base)
            check("remembered in Sophie's name", (made["kind"], made["made_by"], made["until"]), ("hold", "Sophie Tran", base["until"]))
            check("  and on the audit log", len((await s.execute(select(AuditEvent).where(AuditEvent.event_type == "agent_preference.made"))).scalars().all()), 1)
            await refused("the same twice is refused", ex(sophie).preview_change("remember_preference", base), "already")
            note = await ex(sophie).execute("remember_preference", {"recommendation_id": str(ips_rec.id), "kind": "note", "text": "Trust deed allows 70% equity"})
            check("a control's finding takes a note", note["kind"], "note")

            print("\n=== A run, through the real run path ===")
            from app.atlas import registry, runtime
            registry._REGISTRY = registry.build_registry()
            fake = FakeTax()
            fake.households = [chen, nguyen]
            registry._REGISTRY[AgentKey.TAX_INTELLIGENCE] = fake
            r = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            held = (r.context or {}).get("held_by_preference") or []
            check("the Chens' finding is held back, and the run says so", [(h["subject"], h["by"]) for h in held], [("The Chen Family", "Sophie Tran")])
            await s.refresh(open_chen)
            check("their open proposal stays open, not closed as resolved", open_chen.status, RecommendationStatus.PROPOSED)
            raised = (await s.execute(select(Recommendation).where(Recommendation.run_id == r.id))).scalars().all()
            check("the Nguyens' is raised", [x.subject_label for x in raised], ["The Nguyen Family"])
            mem = (raised[0].payload or {}).get("memory") or {}
            check("  with the last decision and its reason on it", (mem.get("last_decision") or {}).get("reason"), "Agreed at the review")

            print("\n=== Ending it ===")
            pref_id = made["id"]
            other = User(id=uuid.uuid4(), firm_id=firm.id, email="r@x", full_name="Rae", role=UserRole.RESEARCH_CIO, hashed_password="x")
            s.add(other)
            await s.flush()
            check("its maker may end it", rows_of(await ex(sophie).preview_change("end_preference", {"preference_id": pref_id}))["After this"], "Its finding is raised again")
            await ex(sophie).execute("end_preference", {"preference_id": pref_id})
            r2 = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            check("ended, the Chens' finding is raised again", (r2.context or {}).get("held_by_preference"), None)
            await refused("an ended one cannot be ended again", ex(sophie).preview_change("end_preference", {"preference_id": pref_id}), "no preference in force")
            listed = await ex(sophie).execute("read_agent_preferences", {})
            check("the note is still listed, the hold is not", [(p["kind"], p["agent_key"]) for p in listed["preferences"]], [("note", "ips_monitoring")])

            print("\n=== Who may use it ===")
            check("a client holds none of these tools", any(k in tools_for_role(UserRole.CLIENT) for k in ("read_agent_preferences", "remember_preference", "end_preference")), False)
            check("an adviser may remember one", "remember_preference" in tools_for_role(UserRole.ADVISER), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
