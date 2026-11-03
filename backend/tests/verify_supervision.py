"""Supervision from a sentence: resolving and escalating a surveillance flag, and re-running the evaluation.

    python -m tests.verify_supervision

Against a throwaway SQLite database. Checks that:

  - only compliance and a branch leader resolve or escalate a flag, on the tool and on the route, and an
    adviser cannot close a flag raised about advice;
  - the Confirm card names the flag, the finding and the reason, and refuses what would only fail;
  - a resolution needs a reason, goes on the ledger once, and does not unpause the agent it paused;
  - the evaluation's card names the agents it would narrow, and running it narrows exactly those;
  - another firm's flag reads as not found.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.agents.catalogue import CATALOGUE
from app.api import provenance as api
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentKey, AgentRunStatus, RecommendationStatus, UserRole
from app.models.governance import AgentRun, LedgerEntry, Recommendation, SurveillanceFlag
from app.models.tenant import AgentConfig, Firm
from seed.ai_governance_backfill import _TOOLS

PASS, FAIL = [], []
R = UserRole
NBA = CATALOGUE[AgentKey.NEXT_BEST_ACTION]["name"]
SUP = ("resolve_surveillance_flag", "escalate_surveillance_flag", "evaluate_agents")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def gating() -> None:
    print("\n=== who holds what ===")
    for key in SUP:
        check(f"{key} pauses", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check(f"{key} is in the AI inventory", key in [t[0] for t in _TOOLS], True)
        for role in (R.ADVISER, R.PARAPLANNER, R.OPERATIONS, R.CLIENT):
            check(f"  {role.value} does not hold {key}", key in tools_for_role(role), False)
    check("flags are compliance's and the branch leader's", TOOLS["resolve_surveillance_flag"].roles_required, {R.COMPLIANCE, R.BRANCH_LEADER})
    check("an administrator does not close one", "resolve_surveillance_flag" in tools_for_role(R.ADMIN), False)
    check("an administrator may re-run the evaluation", "evaluate_agents" in tools_for_role(R.ADMIN), True)
    check("Provenance opens for the people it is for", OPEN_VIEWS["provenance"][2], {R.COMPLIANCE, R.BRANCH_LEADER})


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

            def flag(firm, finding, paused=False):
                f = SurveillanceFlag(id=uuid.uuid4(), firm_id=firm.id, target_agent_key="next_best_action", severity="high",
                                     category="suitability", finding=finding, auto_paused_agent=paused)
                s.add(f)
                return f
            paused_flag = flag(mine, "Recommended equities to a cautious client", paused=True)
            other_flag = flag(mine, "Unclear fee wording")
            theirs_flag = flag(theirs, "Their finding")
            await s.commit()

            def ex(role, firm=mine):
                return ToolExecutors(s, uuid.uuid4(), role, firm.id)

            async def refused(label, call, says=None):
                try:
                    await call
                    check(label, "allowed", "refused")
                except (ExecutorError, HTTPException) as exc:
                    check(label, "refused", "refused")
                    if says:
                        check(f"  and says so: {says}", says in str(getattr(exc, "detail", exc)), True)

            async def ledger(kind):
                return (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == kind))).scalar_one()

            print("\n=== the Confirm card ===")
            ask = {"flag_id": str(paused_flag.id), "note": "Reviewed with the adviser; the client's profile was updated."}
            card = await ex(R.COMPLIANCE).preview_change("resolve_surveillance_flag", ask)
            rows = {r["label"]: r["value"] for r in card["rows"]}
            check("it names the flag and the finding", (rows["Flag"], rows["Finding"]), ("high · suitability", "Recommended equities to a cautious client"))
            check("  the agent", rows["Agent"], NBA)
            check("  the reason", rows["Reason"], ask["note"])
            check("  and that the agent stays paused", rows["Paused agent"], "Stays paused; unpause it in Admin")
            await refused("an adviser is refused on the card", ex(R.ADVISER).preview_change("resolve_surveillance_flag", ask), "compliance")
            await refused("no reason", ex(R.COMPLIANCE).preview_change("resolve_surveillance_flag", {**ask, "note": " "}), "Say why")
            await refused("another firm's flag", ex(R.COMPLIANCE).preview_change("resolve_surveillance_flag", {**ask, "flag_id": str(theirs_flag.id)}), "not found")
            check("the card writes nothing", (await s.get(SurveillanceFlag, paused_flag.id)).resolved, False)

            print("\n=== resolving ===")
            result = await ex(R.COMPLIANCE).execute("resolve_surveillance_flag", ask)
            await s.commit()
            await s.refresh(paused_flag)
            check("it is resolved, with the reason", (paused_flag.resolved, paused_flag.resolution_note), (True, ask["note"]))
            check("it says the agent is still paused", result["agent_still_paused"], True)
            check("it goes on the ledger once", await ledger("flag_resolved"), 1)
            await refused("it cannot be resolved twice", ex(R.COMPLIANCE).execute("resolve_surveillance_flag", ask), "already resolved")
            check("  and the ledger does not grow", await ledger("flag_resolved"), 1)

            print("\n=== escalating ===")
            esc = {"flag_id": str(other_flag.id), "escalated_to": "Head of Advice", "note": "Wording appears across several letters."}
            card = await ex(R.BRANCH_LEADER).preview_change("escalate_surveillance_flag", esc)
            check("the card names who it goes to", {r["label"]: r["value"] for r in card["rows"]}["To"], "Head of Advice")
            await refused("it needs someone to go to", ex(R.BRANCH_LEADER).preview_change("escalate_surveillance_flag", {**esc, "escalated_to": ""}), "who it is escalated to")
            await ex(R.BRANCH_LEADER).execute("escalate_surveillance_flag", esc)
            await s.commit()
            await s.refresh(other_flag)
            check("it is escalated and stays open", (other_flag.escalated, other_flag.escalated_to, other_flag.resolved), (True, "Head of Advice", False))
            await refused("a resolved flag is not escalated", ex(R.COMPLIANCE).preview_change(
                "escalate_surveillance_flag", {**esc, "flag_id": str(paused_flag.id)}), "already resolved")

            print("\n=== the routes hold to the same people ===")
            adviser = SimpleNamespace(role=R.ADVISER, email="a@x")
            admin = SimpleNamespace(role=R.ADMIN, email="ad@x")
            await refused("an adviser cannot resolve on the route", api.resolve_flag(other_flag.id, api.ResolveIn(), firm=mine, db=s, user=adviser), "compliance")
            await refused("an administrator cannot either", api.resolve_flag(other_flag.id, api.ResolveIn(), firm=mine, db=s, user=admin), "compliance")
            await refused("an adviser cannot escalate", api.escalate_flag(other_flag.id, api.EscalateIn(escalated_to="x"), firm=mine, db=s, user=adviser), "compliance")
            await refused("an adviser cannot re-run the evaluation", api.run_evaluation(user=adviser, firm=mine, db=s), "compliance")

            print("\n=== the evaluation ===")
            s.add(AgentConfig(firm_id=mine.id, agent_key=AgentKey.NEXT_BEST_ACTION, enabled=True, default_tier="tier_2"))
            s.add(AgentConfig(firm_id=mine.id, agent_key=AgentKey.CLIENT_CARE, enabled=True, default_tier="tier_1"))
            run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key="next_best_action", status=AgentRunStatus.COMPLETED, tier="tier_2", trigger="t", context={})
            s.add(run)
            await s.flush()
            for i in range(6):
                s.add(Recommendation(firm_id=mine.id, run_id=run.id, agent_key="next_best_action", tier="tier_2",
                                     status=RecommendationStatus.DISMISSED, title=f"t{i}", summary="s", rationale="r",
                                     confidence=0.7, priority=3, payload={}, evidence={}, citations=[]))
            await s.commit()
            card = await ex(R.COMPLIANCE).preview_change("evaluate_agents", {})
            rows = {r["label"]: r["value"] for r in card["rows"]}
            check("the card names the agent it would narrow, and how", rows["Would narrow"], f"{NBA}: Tier 2 to Tier 1")
            check("  and that nothing widens", rows["Widens"], "Never")
            check("the card narrows nothing", (await s.execute(select(AgentConfig.default_tier).where(AgentConfig.agent_key == AgentKey.NEXT_BEST_ACTION))).scalar_one(), "tier_2")
            await refused("an adviser cannot re-run it", ex(R.ADVISER).preview_change("evaluate_agents", {}), "compliance")
            result = await ex(R.ADMIN).execute("evaluate_agents", {})
            await s.commit()
            check("running it narrows exactly that one", result["autonomy_changes"], 1)
            check("  to the tier the card said", (await s.execute(select(AgentConfig.default_tier).where(AgentConfig.agent_key == AgentKey.NEXT_BEST_ACTION))).scalar_one(), "tier_1")
            card = await ex(R.COMPLIANCE).preview_change("evaluate_agents", {})
            check("run again, the card says it would pause it at Tier 1", {r["label"]: r["value"] for r in card["rows"]}["Would narrow"], f"{NBA}: paused")

            print("\n=== a proposal is found by its title, not only its client ===")
            review_run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key="conduct_surveillance", status=AgentRunStatus.COMPLETED,
                                  tier="tier_1", trigger="t", context={})
            s.add(review_run)
            await s.flush()
            review = Recommendation(firm_id=mine.id, run_id=review_run.id, agent_key="conduct_surveillance", tier="tier_1",
                                    status=RecommendationStatus.PROPOSED, title="Conduct surveillance review", summary="s",
                                    rationale="r", confidence=0.7, priority=1, subject_type="firm", subject_label="Mine",
                                    payload={}, evidence={}, citations=[])
            s.add(review)
            await s.commit()

            # A supervision proposal is compliance's to read (decision_rights.SUPERVISION_READERS), so compliance looks.
            async def found(words, role=R.COMPLIANCE):
                r = await ex(role).execute("read_recommendations", {"subject": words})
                return [x["title"] for x in r["recommendations"]]
            check("by its title", await found("Conduct surveillance review"), ["Conduct surveillance review"])
            check("by a word of it, any case", await found("SURVEILLANCE"), ["Conduct surveillance review"])
            check("by its client, as before", "Conduct surveillance review" in await found("Mine"), True)
            check("by its agent", await found("conduct surveillance"), ["Conduct surveillance review"])
            check("not by words it does not have", await found("rebalance"), [])
            check("and an adviser does not see it at all", await found("Conduct surveillance review", R.ADVISER), [])

            print("\n=== a book scan counts what was already open ===")
            from app.atlas import scan_stream

            async def no_households(_s, _firm_id):
                return []

            async def refreshing_run(session, **_kw):
                run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key="next_best_action", status=AgentRunStatus.COMPLETED,
                               tier="tier_1", trigger="book_scan", context={"refreshed": [str(review.id)], "closed": 0})
                session.add(run)
                await session.flush()
                return run
            scan_stream.list_households, scan_stream.run_agent = no_households, refreshing_run
            done = [ev async for ev in scan_stream.scan_book_stream(s, firm=mine, agent_key=AgentKey.NEXT_BEST_ACTION)][-1]
            check("a proposal it refreshed is in the result", [it["title"] for it in done["items"]], ["Conduct surveillance review"])
            check("  marked as already open", (done["items"][0]["still_open"], done["new"], done["still_open"], done["items_surfaced"]), (True, 0, 1, 1))
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
