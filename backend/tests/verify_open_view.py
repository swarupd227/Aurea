"""open_view: Astra opens a view in the workspace's work pane beside the conversation.

    python -m tests.verify_open_view

The tool reads nothing and states nothing - the view loads its own data - so what matters is what it
will name. This checks, against a throwaway SQLite database:

  - it is read-only, held by every staff role and no client;
  - the views it opens are the work pane's (frontend/lib/paneViews.ts), each gated to the roles its page's
    sidebar entry shows (frontend/lib/nav.ts, parsed so the lists cannot drift), administrators seeing all;
  - an id is taken only if a tool returned it earlier in the same turn, and only if it is the caller's
    firm's: an id from memory, from another firm, or with no turn at all is refused;
  - the gateway hands the turn's results to it;
  - the result is a route and nothing else.
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
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import Gateway
from app.models.enums import AgentRunStatus, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Household
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def nav_roles() -> dict[str, set[str]]:
    """href -> roles, read out of frontend/lib/nav.ts (the same parse as verify_page_reads)."""
    nav = (FRONTEND / "lib" / "nav.ts").read_text(encoding="utf-8")
    alias = {"A": "adviser", "P": "paraplanner", "PT": "portfolio_team", "R": "research_cio",
             "C": "compliance", "O": "operations", "B": "branch_leader"}
    out = {}
    for m in re.finditer(r'\{ href: "([^"]+)",[^\n]*?roles: \[([^\]]*)\]', nav):
        out[m.group(1)] = {x.strip().strip('"') if x.strip().startswith('"') else alias[x.strip()]
                           for x in m.group(2).split(",") if x.strip()}
    return out


def gating() -> None:
    print("\n=== read-only, staff only, and each view gated like its page ===")
    tool = TOOLS["open_view"]
    check("open_view changes nothing", tool.change_state, ToolChangeState.NO)
    check("a client holds no open_view", "open_view" in tools_for_role(R.CLIENT), False)
    check("every staff role holds it", {r.value for r in tool.roles_required},
          {"adviser", "paraplanner", "portfolio_team", "research_cio", "compliance", "operations", "branch_leader", "admin"})
    nav = nav_roles()
    for view, (route, _label, roles) in OPEN_VIEWS.items():
        check(f"{view} is gated to the {route} roles", {r.value for r in roles}, nav[route])
    panes = (FRONTEND / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    pane_keys = set(re.findall(r'\{ key: "([a-z_]+)", pattern:', panes))
    pane_bases = set(re.findall(r'base: "([^"]+)"', panes))
    check("the views are the work pane's views", set(OPEN_VIEWS), pane_keys)
    check("on the same pages", {route for route, _l, _r in OPEN_VIEWS.values()}, pane_bases)


async def behaviour() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            mine = Firm(id=uuid.uuid4(), name="Mine", slug="mine")
            theirs = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs")
            s.add_all([mine, theirs])
            await s.flush()
            chen = Household(id=uuid.uuid4(), firm_id=mine.id, name="Chen Family")
            other = Household(id=uuid.uuid4(), firm_id=mine.id, name="Other Family")
            foreign = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Their Client")
            run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key="drift_rebalancing",
                           status=AgentRunStatus.COMPLETED, tier="tier_2", trigger="manual", context={})
            s.add_all([chen, other, foreign, run])
            await s.flush()
            rec = Recommendation(id=uuid.uuid4(), firm_id=mine.id, run_id=run.id, agent_key="drift_rebalancing",
                                 tier="tier_2", status=RecommendationStatus.PROPOSED, title="t", summary="s",
                                 rationale="r", confidence=0.9, priority=1, payload={}, evidence={}, citations=[])
            s.add(rec)
            await s.flush()
            from datetime import date
            from app.models.billing import BillingRun, Invoice
            brun = BillingRun(id=uuid.uuid4(), firm_id=mine.id, as_of=date(2026, 9, 30))
            s.add(brun)
            await s.flush()
            bill = Invoice(id=uuid.uuid4(), firm_id=mine.id, run_id=brun.id, mandate_name="Chen - Balanced",
                           household_id=chen.id, household_name="Chen Family",
                           period_start=date(2026, 7, 1), period_end=date(2026, 9, 30))
            s.add(bill)
            from app.models.onboarding import OnboardingCase
            ocase = OnboardingCase(id=uuid.uuid4(), firm_id=mine.id, prospect_name="Daniel Okonkwo")
            from app.models.engagement import Meeting
            meet = Meeting(id=uuid.uuid4(), firm_id=mine.id, household_id=chen.id, title="Annual review")
            s.add_all([ocase, meet])
            await s.commit()

            def ex(role, f=mine):
                return ToolExecutors(s, uuid.uuid4(), role, f.id)

            async def refused(label, call):
                try:
                    await call
                    check(label, "opened", "refused")
                except ExecutorError:
                    check(label, "refused", "refused")

            # What a turn's earlier tools returned, as the gateway collects them.
            searched = [{"tool_key": "search_households", "inputs": {"query": "chen"},
                         "result": {"results": [{"household_id": str(chen.id), "name": "Chen Family"}]}}]
            listed = [{"tool_key": "read_recommendations", "inputs": {},
                       "result": {"recommendations": [{"id": str(rec.id), "title": "t"}]}}]
            smuggled = [{"tool_key": "search_households", "inputs": {},
                         "result": {"results": [{"household_id": str(foreign.id)}]}}]

            print("\n=== Views without an id ===")
            c = await ex(R.ADVISER).open_view({"view": "cockpit"})
            check("the cockpit opens for an adviser", c["view"], "/studio")
            check("the result is a route and nothing else", sorted(c), ["kind", "message", "view"])
            check("recommendations, no id", (await ex(R.COMPLIANCE).open_view({"view": "Recommendations"}))["view"], "/studio/review")
            check("clients", (await ex(R.OPERATIONS).open_view({"view": "clients"}))["view"], "/studio/clients")
            check("an administrator opens any view", (await ex(R.ADMIN).open_view({"view": "cockpit"}))["view"], "/studio")
            await refused("compliance has no cockpit", ex(R.COMPLIANCE).open_view({"view": "cockpit"}))
            await refused("compliance has no client list", ex(R.COMPLIANCE).open_view({"view": "clients"}))
            await refused("research has no recommendations view", ex(R.RESEARCH_CIO).open_view({"view": "recommendations"}))
            check("billing opens for operations", (await ex(R.OPERATIONS).open_view({"view": "billing"}))["view"], "/studio/billing")
            check("and for compliance", (await ex(R.COMPLIANCE).open_view({"view": "billing"}))["view"], "/studio/billing")
            await refused("a paraplanner has no billing view", ex(R.PARAPLANNER).open_view({"view": "billing"}))
            await refused("an unknown view", ex(R.ADVISER).open_view({"view": "estate"}))

            print("\n=== An id only from this turn, and only this firm's ===")
            got = await ex(R.ADVISER).open_view({"view": "client", "household_id": str(chen.id)}, turn=searched)
            check("a client a tool returned this turn opens", got["view"], f"/studio/clients/{chen.id}")
            await refused("a client no tool returned this turn",
                          ex(R.ADVISER).open_view({"view": "client", "household_id": str(other.id)}, turn=searched))
            await refused("a client with no turn at all (outside the gateway)",
                          ex(R.ADVISER).execute("open_view", {"view": "client", "household_id": str(chen.id)}))
            await refused("another firm's client, even if a result carried its id",
                          ex(R.ADVISER).open_view({"view": "client", "household_id": str(foreign.id)}, turn=smuggled))
            await refused("a client view with no id", ex(R.ADVISER).open_view({"view": "client"}, turn=searched))
            await refused("a malformed id", ex(R.ADVISER).open_view({"view": "client", "household_id": "chen"}, turn=searched))
            r = await ex(R.PORTFOLIO_TEAM).open_view({"view": "recommendations", "recommendation_id": str(rec.id)}, turn=listed)
            check("a proposal read this turn opens at the top", r["view"], f"/studio/review?rec={rec.id}")
            await refused("a proposal not read this turn",
                          ex(R.PORTFOLIO_TEAM).open_view({"view": "recommendations", "recommendation_id": str(rec.id)}, turn=searched))
            await refused("the other firm cannot open this firm's proposal",
                          ex(R.PORTFOLIO_TEAM, theirs).open_view({"view": "recommendations", "recommendation_id": str(rec.id)}, turn=listed))

            billed = [{"tool_key": "explain_invoice", "inputs": {}, "result": {"id": str(bill.id), "number": None}}]
            b = await ex(R.OPERATIONS).open_view({"view": "billing", "invoice_id": str(bill.id)}, turn=billed)
            check("a bill a tool returned this turn opens", b["view"], f"/studio/billing?invoice={bill.id}")
            await refused("a bill no tool returned this turn",
                          ex(R.OPERATIONS).open_view({"view": "billing", "invoice_id": str(bill.id)}, turn=searched))
            await refused("the other firm cannot open this firm's bill",
                          ex(R.OPERATIONS, theirs).open_view({"view": "billing", "invoice_id": str(bill.id)}, turn=billed))

            check("valuations opens for the portfolio team", (await ex(R.PORTFOLIO_TEAM).open_view({"view": "valuations"}))["view"], "/studio/valuations")
            await refused("an adviser has no valuations view", ex(R.ADVISER).open_view({"view": "valuations"}))
            check("the onboarding board opens for operations", (await ex(R.OPERATIONS).open_view({"view": "onboarding"}))["view"], "/studio/onboarding")
            await refused("a portfolio team member has no onboarding view", ex(R.PORTFOLIO_TEAM).open_view({"view": "onboarding"}))
            cased = [{"tool_key": "read_onboarding_cases", "inputs": {}, "result": {"cases": [{"id": str(ocase.id)}]}}]
            o = await ex(R.OPERATIONS).open_view({"view": "onboarding_case", "case_id": str(ocase.id)}, turn=cased)
            check("a case read this turn opens", o["view"], f"/studio/onboarding/{ocase.id}")
            await refused("a case not read this turn",
                          ex(R.OPERATIONS).open_view({"view": "onboarding_case", "case_id": str(ocase.id)}, turn=searched))
            await refused("a case view with no id", ex(R.OPERATIONS).open_view({"view": "onboarding_case"}, turn=cased))
            await refused("the other firm cannot open this firm's case",
                          ex(R.OPERATIONS, theirs).open_view({"view": "onboarding_case", "case_id": str(ocase.id)}, turn=cased))
            check("meetings opens for a paraplanner", (await ex(R.PARAPLANNER).open_view({"view": "meetings"}))["view"], "/studio/meetings")
            await refused("operations has no meetings view", ex(R.OPERATIONS).open_view({"view": "meetings"}))
            met = [{"tool_key": "read_meetings", "inputs": {}, "result": {"meetings": [{"id": str(meet.id)}]}}]
            mv = await ex(R.ADVISER).open_view({"view": "meeting", "meeting_id": str(meet.id)}, turn=met)
            check("a meeting read this turn opens", mv["view"], f"/studio/meetings/{meet.id}")
            await refused("a meeting not read this turn",
                          ex(R.ADVISER).open_view({"view": "meeting", "meeting_id": str(meet.id)}, turn=searched))
            await refused("the other firm cannot open this firm's meeting",
                          ex(R.ADVISER, theirs).open_view({"view": "meeting", "meeting_id": str(meet.id)}, turn=met))

            print("\n=== The gateway passes the turn ===")
            gw = Gateway(s, uuid.uuid4(), R.ADVISER, mine.id)
            g = await gw._execute_tool("open_view", {"view": "client", "household_id": str(chen.id)}, turn=searched)
            check("through the gateway, with this turn's results", g["view"], f"/studio/clients/{chen.id}")
            try:
                await gw._execute_tool("open_view", {"view": "client", "household_id": str(chen.id)}, turn=[])
                check("through the gateway, before any tool found it", "opened", "refused")
            except ExecutorError:
                check("through the gateway, before any tool found it", "refused", "refused")
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
