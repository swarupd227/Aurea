"""The conversation's reads for page-first areas: Agent history, Capacity, Analytics, Onboarding
cases and Provenance.

    python -m tests.verify_page_reads

Each tool wraps the function its page's route already calls, is scoped to the caller's firm and is
gated to the roles that page's sidebar entry shows (frontend/lib/nav.ts). This checks, against a
throwaway SQLite database:

  - each tool is read-only and is held by exactly the nav roles (plus administrators, who see every
    view) - parsed out of nav.ts so the two lists cannot drift;
  - clients hold none of them;
  - each returns the figures the page shows, for the caller's firm and no other;
  - honesty: the capacity hours come with the basis of the estimate; an unmeasured figure is empty.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentRunStatus, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation, SurveillanceFlag
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole

# The page each tool stands in for, by the route nav.ts lists it under.
PAGES = {
    "read_agent_history": "/studio/agent-history",
    "read_capacity": "/studio/capacity",
    "read_analytics": "/studio/analytics",
    "read_onboarding_cases": "/studio/onboarding",
    "read_meetings": "/studio/meetings",
    "read_tasks": "/studio/tasks",
    "read_reports": "/studio/reports",
    "read_provenance": "/provenance",
}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def nav_roles() -> dict[str, set[str]]:
    """href -> roles, read out of frontend/lib/nav.ts."""
    nav = (Path(__file__).resolve().parents[2] / "frontend" / "lib" / "nav.ts").read_text(encoding="utf-8")
    alias = dict(re.findall(r'\b([A-Z]{1,2}) = "([a-z_]+)"', nav.split("export const VIEWS")[0]))
    alias.update({"A": "adviser", "P": "paraplanner", "PT": "portfolio_team", "R": "research_cio",
                  "C": "compliance", "O": "operations", "B": "branch_leader"})
    out = {}
    for m in re.finditer(r'\{ href: "([^"]+)",[^\n]*?roles: \[([^\]]*)\]', nav):
        out[m.group(1)] = {x.strip().strip('"') if x.strip().startswith('"') else alias[x.strip()]
                           for x in m.group(2).split(",") if x.strip()}
    return out


def gating() -> None:
    print("\n=== read-only, and held by the roles the page's sidebar entry shows ===")
    nav = nav_roles()
    for tool_key, href in PAGES.items():
        tool = TOOLS[tool_key]
        check(f"{tool_key} changes nothing", tool.change_state, ToolChangeState.NO)
        want = nav[href] | {"admin"}
        check(f"{tool_key} is held by the {href} roles (and admin)", {r.value for r in tool.roles_required}, want)
        check(f"a client holds no {tool_key}", tool_key in tools_for_role(R.CLIENT), False)
        ok, _ = validate_tool_call(R.RESEARCH_CIO if R.RESEARCH_CIO not in tool.roles_required else R.CLIENT, tool_key, {})
        check(f"{tool_key} refuses a role outside that list", ok, False)


async def behaviour() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            mine = Firm(id=uuid.uuid4(), name="Mine", slug="mine", base_currency="NZD")
            theirs = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs")
            s.add_all([mine, theirs])
            await s.flush()

            def run(f, key, status, tier="tier_2"):
                r = AgentRun(id=uuid.uuid4(), firm_id=f.id, agent_key=key, status=status, tier=tier,
                             trigger="manual", context={})
                s.add(r)
                return r
            r1 = run(mine, "drift_rebalancing", AgentRunStatus.COMPLETED)
            run(mine, "drift_rebalancing", AgentRunStatus.FAILED)
            run(mine, "next_best_action", AgentRunStatus.AWAITING_APPROVAL)
            run(theirs, "drift_rebalancing", AgentRunStatus.COMPLETED)
            run(theirs, "client_care", AgentRunStatus.COMPLETED)
            await s.flush()
            for status in (RecommendationStatus.PROPOSED, RecommendationStatus.APPROVED, RecommendationStatus.DISMISSED):
                s.add(Recommendation(id=uuid.uuid4(), firm_id=mine.id, run_id=r1.id, agent_key="drift_rebalancing",
                                     tier="tier_2", status=status, title="t", summary="s", rationale="r",
                                     confidence=0.9, priority=1, payload={}, evidence={}, citations=[]))
            s.add(Recommendation(id=uuid.uuid4(), firm_id=theirs.id, run_id=r1.id, agent_key="client_care",
                                 tier="tier_2", status=RecommendationStatus.PROPOSED, title="t", summary="s",
                                 rationale="r", confidence=0.9, priority=1, payload={}, evidence={}, citations=[]))
            s.add(OnboardingCase(id=uuid.uuid4(), firm_id=mine.id, prospect_name="Ngata Trust", status="intake",
                                 registration_type="trust"))
            s.add(OnboardingCase(id=uuid.uuid4(), firm_id=mine.id, prospect_name="Done Person", status="approved"))
            s.add(OnboardingCase(id=uuid.uuid4(), firm_id=theirs.id, prospect_name="Their Prospect", status="intake"))
            s.add(SurveillanceFlag(id=uuid.uuid4(), firm_id=mine.id, target_agent_key="drift_rebalancing",
                                   severity="high", category="suitability", finding="x", auto_paused_agent=True))
            s.add(SurveillanceFlag(id=uuid.uuid4(), firm_id=mine.id, severity="low", category="conduct",
                                   finding="y", resolved=True))
            s.add(SurveillanceFlag(id=uuid.uuid4(), firm_id=theirs.id, severity="high", category="conduct", finding="z"))
            await s.commit()
            from app.provenance import ledger
            for f, n in ((mine, 3), (theirs, 5)):
                for i in range(n):
                    await ledger.append_entry(s, firm_id=f.id, event_type="decision", agent_key="drift_rebalancing",
                                              run_id=None, recommendation_id=None, actor="test", content={"i": i})
            await s.commit()

            def ex(role, f=mine):
                return ToolExecutors(s, uuid.uuid4(), role, f.id)

            print("\n=== Agent history ===")
            h = await ex(R.ADVISER).execute("read_agent_history", {})
            check("only this firm's runs", h["shown"], 3)
            check("counted by status", h["by_status"], {"completed": 1, "failed": 1, "awaiting_approval": 1})
            check("each run carries the agent's name", {r["agent_name"] for r in h["runs"]},
                  {"Drift & Tax-Managed Rebalancing", "Next-Best-Action & Growth"})
            one = await ex(R.ADVISER).execute("read_agent_history", {"agent_key": "drift_rebalancing", "status": "failed"})
            check("filters by agent and status", [r["status"] for r in one["runs"]], ["failed"])
            check("the other firm sees its own two", (await ex(R.ADVISER, theirs).execute("read_agent_history", {}))["shown"], 2)
            try:
                await ex(R.ADVISER).execute("read_agent_history", {"agent_key": "nope"})
                check("an unknown agent is refused", "answered", "refused")
            except ExecutorError:
                check("an unknown agent is refused", "refused", "refused")

            print("\n=== Capacity ===")
            c = await ex(R.BRANCH_LEADER).execute("read_capacity", {})
            check("runs counted for this firm", c["total_agent_runs"], 3)
            check("proposals by status", {k: v for k, v in c["recommendations_by_status"].items() if v},
                  {"proposed": 1, "approved": 1, "dismissed": 1})
            check("decisions made", c["decisions_made"], 2)
            check("open items", c["open_items"], 1)
            check("hours = (runs + decisions) x 25 min", c["estimated_hours_reclaimed"], round((3 + 2) * 25 / 60, 1))
            check("the hours are labelled an estimate with their basis", c["estimate"], {"minutes_per_item": 25, "measured": False})
            check("and the sentence says so", "estimate" in c["message"] and "not a measurement" in c["message"], True)

            print("\n=== Analytics ===")
            a = await ex(R.PORTFOLIO_TEAM).execute("read_analytics", {})
            check("a headline block with the firm's currency", (sorted(a["headline"])[:2], a["currency"]),
                  (["agents_healthy", "agents_total"], "NZD"))
            check("no households means no clients, not an invented number", a["headline"]["clients"], 0)
            check("an unmeasurable return is empty, not zero", a["headline"]["goals_on_track_pct"], None)

            print("\n=== Onboarding cases ===")
            o = await ex(R.OPERATIONS).execute("read_onboarding_cases", {})
            check("open cases only by default, this firm only", [x["prospect_name"] for x in o["cases"]], ["Ngata Trust"])
            check("summary counts the whole board", (o["summary"]["total"], o["summary"]["open"]), (2, 1))
            check("each case carries its four tracks", [t["code"] for t in o["cases"][0]["tracks"]], ["A", "B", "C", "D"])
            allc = await ex(R.OPERATIONS).execute("read_onboarding_cases", {"status": "all"})
            check("all includes the approved case", sorted(x["prospect_name"] for x in allc["cases"]), ["Done Person", "Ngata Trust"])
            check("the other firm sees only its own", [x["prospect_name"] for x in
                  (await ex(R.OPERATIONS, theirs).execute("read_onboarding_cases", {}))["cases"]], ["Their Prospect"])

            print("\n=== Meetings ===")
            # Households only here: the capacity checks above count a firm with none.
            from app.models.engagement import Meeting
            from app.models.graph import Household
            hh_mine = Household(id=uuid.uuid4(), firm_id=mine.id, name="Chen Family")
            hh_other = Household(id=uuid.uuid4(), firm_id=mine.id, name="Patel Family")
            hh_theirs = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Their Client")
            s.add_all([hh_mine, hh_other, hh_theirs])
            await s.flush()
            s.add(Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=hh_mine.id, title="Annual review",
                          brief={"agenda": ["Drift", "Goals"]}, transcript="We talked.",
                          notes={"summary": ["ok"], "sentiment": "positive", "action_items": ["Send pack"]}))
            s.add(Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=hh_other.id, title="Intro"))
            from datetime import timedelta
            from app.core.db import utcnow
            s.add(Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=hh_other.id, title="Missed check-in",
                          scheduled_at=utcnow() - timedelta(days=90), status="prepped"))
            s.add(Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=hh_other.id, title="Next check-in",
                          scheduled_at=utcnow() + timedelta(days=5)))
            s.add(Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=hh_mine.id, title="Last year's review",
                          scheduled_at=utcnow() - timedelta(days=300), status="completed"))
            s.add(Meeting(id=uuid.uuid4(), firm_id=theirs.id, household_id=hh_theirs.id, title="Theirs"))
            await s.commit()
            mt = await ex(R.ADVISER).execute("read_meetings", {})
            check("this firm's meetings only", sorted(x["title"] for x in mt["meetings"]),
                  ["Annual review", "Intro", "Last year's review", "Missed check-in", "Next check-in"])
            passed = {x["title"]: x["date_passed"] for x in mt["meetings"]}
            check("a meeting not held whose date has gone by says so",
                  (passed["Missed check-in"], passed["Next check-in"], passed["Last year's review"]), (True, False, False))
            check("  and is not counted as upcoming", (mt["upcoming"], mt["date_passed"]), (1, 1))
            check("  and the sentence says so", "1 not held although the date has passed" in mt["message"], True)
            review = next(x for x in mt["meetings"] if x["title"] == "Annual review")
            check("a meeting says whether it was briefed, and its agenda", (review["brief_prepared"], review["agenda"]), (True, ["Drift", "Goals"]))
            check("whether a transcript is on file, and the follow-ups", (review["has_transcript"], review["action_items"]), (True, ["Send pack"]))
            check("and names its client", review["household_name"], "Chen Family")
            one = await ex(R.ADVISER).execute("read_meetings", {"household_id": str(hh_other.id)})
            check("a household filters to its own", sorted(x["title"] for x in one["meetings"]),
                  ["Intro", "Missed check-in", "Next check-in"])
            try:
                await ex(R.ADVISER).execute("read_meetings", {"household_id": str(hh_theirs.id)})
                check("another firm's household is refused", "read", "refused")
            except ExecutorError:
                check("another firm's household is refused", "refused", "refused")

            print("\n=== Provenance ===")
            p = await ex(R.COMPLIANCE).execute("read_provenance", {})
            check("the ledger verifies over this firm's entries only", (p["ledger"]["valid"], p["ledger"]["count"]), (True, 3))
            check("recent entries are this firm's", len(p["ledger"]["recent"]), 3)
            check("one open flag, resolved ones left out", (p["surveillance"]["open"], p["surveillance"]["by_severity"]),
                  (1, {"high": 1}))
            check("and it names the agent it paused", p["surveillance"]["flags"][0]["target_agent_name"],
                  "Drift & Tax-Managed Rebalancing")
            check("the other firm's ledger is its own", (await ex(R.COMPLIANCE, theirs).execute("read_provenance", {}))["ledger"]["count"], 5)
    finally:
        await engine.dispose()


def main() -> int:
    gating()
    asyncio.run(behaviour())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
