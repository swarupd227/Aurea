"""Client reports from a sentence: the read, approving through a proposal, and withdrawing.

    python -m tests.verify_reports

Against a throwaway SQLite database, with the model scripted. Checks that:

  - who holds each tool, and that withdrawing pauses;
  - the read is this firm's reports, says what can happen to each next, and gives one report's text;
  - a rerun rewrites the draft still waiting for a decision instead of stranding it;
  - the Confirm card names the report; nothing moves until it is confirmed, it moves once, and
    "Not now" moves nothing;
  - a client reads a report only once it is approved, and stops when it is withdrawn;
  - a report with no proposal behind it cannot be withdrawn, and nothing here publishes one;
  - a report, a household or a proposal from another firm reads as not found;
  - a client reads only their own household's report;
  - the page's routes say the same as the tool.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.agents import research_reporting as rr
from app.api import canvas, engage
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, reports
from app.core.db import Base, utcnow
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm import gateway as gw_mod
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import Gateway
from app.models.engagement import ClientReport
from app.models.enums import AgentKey, AgentRunStatus, RecommendationStatus, ReportStatus, UserRole
from app.models.governance import AgentRun, LedgerEntry, Recommendation
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm
from app.models.thread import PendingAction, Thread, ThreadKind

PASS, FAIL = [], []
R = UserRole
AGENT = AgentKey.RESEARCH_REPORTING.value


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def script(*rounds):
    it = iter(rounds)

    async def stream(**_kw):
        for ev in next(it):
            yield ev
    return stream


def tool_round(tool: str, inputs: dict):
    return [{"type": "done", "tool_uses": [{"id": "t1", "name": tool, "input": inputs}],
             "content_blocks": [{"type": "tool_use", "id": "t1", "name": tool, "input": inputs}]}]


def say(text: str):
    return [{"type": "text", "text": text}, {"type": "done", "tool_uses": [], "content_blocks": []}]


def gating() -> None:
    print("\n=== who holds what ===")
    check("the read changes nothing", TOOLS["read_reports"].change_state, ToolChangeState.NO)
    check("withdrawing pauses", TOOLS["withdraw_report"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("the read follows the page's sidebar", TOOLS["read_reports"].roles_required, {R.ADVISER, R.PARAPLANNER, R.RESEARCH_CIO, R.ADMIN})
    check("withdrawing is for the people who work on reports", TOOLS["withdraw_report"].roles_required, {R.ADVISER, R.PARAPLANNER, R.RESEARCH_CIO})
    check("an administrator cannot take a report from a client", "withdraw_report" in tools_for_role(R.ADMIN), False)
    check("research can decide what it can see", "decide_recommendation" in tools_for_role(R.RESEARCH_CIO), True)
    for key in ("read_reports", "withdraw_report", "decide_recommendation"):
        check(f"a client holds no {key}", key in tools_for_role(R.CLIENT), False)
    check("the views open for the same people as the page", OPEN_VIEWS["reports"][2], {R.ADVISER, R.PARAPLANNER, R.RESEARCH_CIO})
    check("  and one report", OPEN_VIEWS["report"][2], {R.ADVISER, R.PARAPLANNER, R.RESEARCH_CIO})
    check("no tool publishes a report directly", [k for k in TOOLS if "publish" in k], [])


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
            today = firm_time.today(mine)

            def person(firm, name, role=R.ADVISER, **kw):
                return User(id=uuid.uuid4(), firm_id=firm.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=role, is_active=True, **kw)
            sophie = person(mine, "Sophie Tran")
            res = person(mine, "Rae Search", R.RESEARCH_CIO)
            admin = person(mine, "Platform Admin", R.ADMIN)
            chen = Household(id=uuid.uuid4(), firm_id=mine.id, name="Chen Family")
            lee = Household(id=uuid.uuid4(), firm_id=mine.id, name="Lee Family")
            their_hh = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Their Client")
            s.add_all([sophie, res, admin, chen, lee, their_hh])
            await s.flush()
            wei = Person(id=uuid.uuid4(), firm_id=mine.id, household_id=chen.id, full_name="Wei Chen")
            ming = Person(id=uuid.uuid4(), firm_id=mine.id, household_id=lee.id, full_name="Ming Lee")
            s.add_all([wei, ming])
            await s.flush()
            client_wei = person(mine, "Wei Chen", R.CLIENT, person_id=wei.id)
            client_ming = person(mine, "Ming Lee", R.CLIENT, person_id=ming.id)
            client_nobody = person(mine, "No Person", R.CLIENT)
            s.add_all([client_wei, client_ming, client_nobody])

            def report(firm, hh, title, status=ReportStatus.DRAFT, **kw):
                r = ClientReport(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, title=title, period="Q3 2026",
                                 status=status, sections=[{"heading": "Summary", "body": f"About {title}."}], data={}, **kw)
                s.add(r)
                return r
            seeded_draft = report(mine, lee, "Seeded draft")
            seeded_ready = report(mine, lee, "Seeded review", ReportStatus.CLIENT_READY, published_at=utcnow())
            theirs_report = report(theirs, their_hh, "Their report")
            await s.commit()

            def ex(user=sophie):
                return ToolExecutors(s, user.id, user.role, user.firm_id)

            async def refused(label, call, says=None):
                try:
                    await call
                    check(label, "allowed", "refused")
                except ExecutorError as exc:
                    check(label, "refused", "refused")
                    if says:
                        check(f"  and says so: {says}", says in str(exc), True)

            async def proposal_for(r, firm=mine, status=RecommendationStatus.PROPOSED, hh=None):
                run = AgentRun(id=uuid.uuid4(), firm_id=firm.id, agent_key=AGENT, status=AgentRunStatus.COMPLETED,
                               tier="tier_1", trigger="test", context={})
                s.add(run)
                await s.flush()
                rec = Recommendation(id=uuid.uuid4(), firm_id=firm.id, run_id=run.id, agent_key=AGENT, tier="tier_1",
                                     status=status, title=f"Client report — {(hh or chen).name}", summary="s", rationale="r",
                                     confidence=0.8, priority=3, subject_type="household", subject_id=(hh or chen).id,
                                     subject_label=(hh or chen).name, payload={"report_id": str(r.id)}, evidence={}, citations=[])
                s.add(rec)
                await s.commit()
                return rec

            print("\n=== the agent's draft ===")
            run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key=AGENT, status=AgentRunStatus.COMPLETED,
                           tier="tier_1", trigger="test", context={})
            s.add(run)
            await s.flush()
            ctx = AgentContext(session=s, firm=mine, run=run, tier="tier_1", guardrails={}, config={},
                               subject=Subject("household", chen.id, chen.name))
            rr.goal_projections = lambda _b: []
            rr.positions_of = lambda _b: []
            agent = rr.ResearchReportingAgent()

            async def commentary(*_a, **_k):
                return "Positioned as agreed."
            agent._commentary = commentary

            def sensed(total):
                return {"applicable": True, "household_id": str(chen.id), "citations": [], "brain": {
                    "household": {"name": chen.name, "values": {}},
                    "totals": {"total_value": total, "by_asset_class": {"equity": total * 0.6, "cash": total * 0.4}, "data_confidence": 0.9},
                }}

            async def count_reports():
                return (await s.execute(select(func.count()).select_from(ClientReport).where(
                    ClientReport.firm_id == mine.id, ClientReport.household_id == chen.id))).scalar_one()

            first = (await agent.think(ctx, sensed(100_000)))[0]
            await s.commit()
            draft_id = uuid.UUID(first.payload["report_id"])
            check("a run drafts one report", await count_reports(), 1)
            drafted = await s.get(ClientReport, draft_id)
            check("as at the firm's date", drafted.period, f"As at {today.isoformat()}")
            live = Recommendation(id=uuid.uuid4(), firm_id=mine.id, run_id=run.id, agent_key=AGENT, tier="tier_1",
                                  status=RecommendationStatus.PROPOSED, title=first.title, summary=first.summary,
                                  rationale=first.rationale, confidence=first.confidence, priority=first.priority,
                                  subject_type="household", subject_id=chen.id, subject_label=chen.name,
                                  payload=first.payload, evidence={}, citations=[])
            s.add(live)
            await s.commit()
            second = (await agent.think(ctx, sensed(200_000)))[0]
            await s.commit()
            check("a rerun rewrites the waiting draft, not a second one", await count_reports(), 1)
            check("  under the same report", second.payload["report_id"], str(draft_id))
            await s.refresh(drafted)
            check("  with the newer figures", "$200,000" in drafted.sections[0]["body"], True)

            print("\n=== the read ===")
            r = await ex().execute("read_reports", {})
            by_title = {x["title"]: x for x in r["reports"]}
            check("this firm's reports only", sorted(by_title), [f"Portfolio review — {chen.name}", "Seeded draft", "Seeded review"])
            check("counted", (r["drafts"], r["client_ready"], r["awaiting_decision"]), (2, 1, 1))
            check("the message counts them", r["message"], "3 report(s). 2 draft, 1 with clients, 1 awaiting a decision.")
            waiting = by_title[f"Portfolio review — {chen.name}"]
            check("a draft awaiting a decision names its proposal", (waiting["awaiting_decision"], waiting["recommendation_id"]), (True, str(live.id)))
            check("a seeded draft has none", (by_title["Seeded draft"]["awaiting_decision"], by_title["Seeded draft"]["recommendation_id"]), (False, None))
            check("the list carries no report text", "sections" in waiting, False)
            check("a filter", [x["title"] for x in (await ex().execute("read_reports", {"status": "client_ready"}))["reports"]], ["Seeded review"])
            check("a household's", sorted(x["title"] for x in (await ex().execute("read_reports", {"household_id": str(lee.id)}))["reports"]), ["Seeded draft", "Seeded review"])
            check("a limit is honest about the rest", (lambda x: (x["shown"], x["total"]))(await ex().execute("read_reports", {"limit": 1})), (1, 3))
            one = await ex().execute("read_reports", {"report_id": str(seeded_draft.id)})
            check("one report gives its text", one["reports"][0]["sections"][0]["body"], "About Seeded draft.")
            check("research reads them too", (await ex(res).execute("read_reports", {}))["total"], 3)
            await refused("an unknown status", ex().execute("read_reports", {"status": "urgent"}))
            await refused("another firm's report", ex().execute("read_reports", {"report_id": str(theirs_report.id)}), "not found")
            await refused("another firm's household", ex().execute("read_reports", {"household_id": str(their_hh.id)}), "not found")

            print("\n=== opening ===")
            found = [{"tool_key": "read_reports", "inputs": {}, "result": {"reports": [{"id": str(seeded_draft.id)}]}}]
            check("the list opens", (await ex().open_view({"view": "reports"}))["view"], "/studio/reports")
            check("a report read this turn opens", (await ex().open_view({"view": "report", "report_id": str(seeded_draft.id)}, turn=found))["view"],
                  f"/studio/reports/{seeded_draft.id}")
            await refused("a report not read this turn", ex().open_view({"view": "report", "report_id": str(seeded_draft.id)}, turn=[]), "read_reports")
            await refused("the other firm's", ex().open_view({"view": "report", "report_id": str(theirs_report.id)},
                                                             turn=[{"result": {"id": str(theirs_report.id)}}]), "not found")

            print("\n=== approving, through the Confirm card ===")
            async def thread(user):
                t = Thread(firm_id=mine.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=user.id, title="t")
                s.add(t)
                await s.commit()
                return t

            async def ask(tool, inputs, user=sophie):
                t = await thread(user)
                gw_mod.llm_service.stream = script(tool_round(tool, inputs), say("Not offered."))
                events = [e async for e in Gateway(s, user.id, user.role, user.firm_id).send_message(t.id, "please")]
                return t, next((e for e in events if e["type"] == "pending_action"), None)

            async def settle(t, confirm, user=sophie):
                pending = (await s.execute(select(PendingAction).where(PendingAction.thread_id == t.id))).scalars().first()
                gw_mod.llm_service.stream = script(say("Done."))
                return await Gateway(s, user.id, user.role, user.firm_id).confirm_pending_action(pending.id, confirm)

            def rows(ev):
                return {r["label"]: r["value"] for r in ev["preview"]["rows"]}

            async def client_reads(r, client=client_wei):
                return await canvas.canvas_report(r.id, user=client, firm=mine, db=s)

            async def client_refused(label, r, client, status):
                try:
                    await client_reads(r, client)
                    check(label, "read", status)
                except HTTPException as exc:
                    check(label, exc.status_code, status)

            await client_refused("a client cannot read a draft", drafted, client_wei, 404)
            approve = {"recommendation_id": str(live.id), "action": "approve"}
            t1, ev = await ask("decide_recommendation", approve)
            check("the card names the report and what approving does", rows(ev), {
                "Report": f"Portfolio review — {chen.name}", "Client": chen.name, "Period": f"As at {today.isoformat()}",
                "Decision": "Approve, and the client can read it"})
            await s.refresh(drafted)
            check("nothing changed yet", str(drafted.status), "draft")
            await settle(t1, False)
            await s.refresh(drafted)
            check("Not now changes nothing", str(drafted.status), "draft")

            t2, ev = await ask("decide_recommendation", approve)
            await settle(t2, True)
            await s.refresh(drafted)
            check("confirming publishes it", (str(drafted.status), drafted.published_at is not None), ("client_ready", True))
            check("the client can read it", (await client_reads(drafted))["title"], drafted.title)
            await client_refused("another household's client cannot", drafted, client_ming, 403)
            await client_refused("nor a client tied to no household", drafted, client_nobody, 403)
            again = await ex().execute("read_reports", {"report_id": str(draft_id)})
            check("the read says it can be taken back", again["reports"][0]["can_withdraw"], True)
            t3, ev = await ask("decide_recommendation", approve)
            check("it cannot be decided twice", ev, None)

            print("\n=== withdrawing, through the Confirm card ===")
            t4, ev = await ask("withdraw_report", {"report_id": str(draft_id)})
            check("the card names the report", rows(ev), {
                "Report": drafted.title, "Client": chen.name, "Period": f"As at {today.isoformat()}",
                "Published": firm_time.spoken(firm_time.today(mine, drafted.published_at))})
            await settle(t4, False)
            await s.refresh(drafted)
            check("Not now leaves it with the client", str(drafted.status), "client_ready")
            t5, ev = await ask("withdraw_report", {"report_id": str(draft_id), "note": "Figures need another look"})
            await settle(t5, True)
            await s.refresh(drafted)
            check("confirming takes it back", (str(drafted.status), drafted.published_at), ("draft", None))
            await client_refused("the client can no longer read it", drafted, client_wei, 404)
            await s.refresh(live)
            check("it is the proposal that was rolled back", str(live.status), "rolled_back")
            check("and the ledger says who and why", (await s.execute(select(LedgerEntry).where(
                LedgerEntry.recommendation_id == live.id, LedgerEntry.event_type == "rollback"))).scalars().one().actor, "Sophie Tran (adviser)")
            t6, ev = await ask("withdraw_report", {"report_id": str(draft_id)})
            check("it cannot be withdrawn twice", ev, None)
            after = (await ex().execute("read_reports", {"report_id": str(draft_id)}))["reports"][0]
            check("the read says what became of it", (after["status"], after["proposal_status"], after["can_withdraw"], after["awaiting_decision"]),
                  ("draft", "rolled_back", False, False))
            await refused("a seeded report has no proposal to roll back", ex().preview_change("withdraw_report", {"report_id": str(seeded_ready.id)}), "no approved proposal")
            await refused("  at execution too", ex().execute("withdraw_report", {"report_id": str(seeded_ready.id)}), "no approved proposal")
            await refused("a draft is not with the client", ex().preview_change("withdraw_report", {"report_id": str(seeded_draft.id)}), "not with the client")
            await refused("another firm's report", ex().preview_change("withdraw_report", {"report_id": str(theirs_report.id)}), "not found")

            print("\n=== a rerun after a decision starts a new draft ===")
            third = (await agent.think(ctx, sensed(300_000)))[0]
            await s.commit()
            check("a withdrawn draft is left as it stands", third.payload["report_id"] != str(draft_id), True)
            check("  beside the new one", await count_reports(), 2)
            dropped = await proposal_for(await s.get(ClientReport, uuid.UUID(third.payload["report_id"])), status=RecommendationStatus.DISMISSED)
            check("a dismissed proposal leaves nothing waiting", await reports.waiting_draft(s, mine.id, chen.id), None)
            check("  and the read says dismissed", (await ex().execute("read_reports", {"report_id": third.payload["report_id"]}))["reports"][0]["proposal_status"], str(dropped.status))

            print("\n=== other proposals keep their plain card ===")
            check("an id that does not resolve", await ex().preview_change("decide_recommendation", {"recommendation_id": "nonsense"}), None)
            other_run = AgentRun(id=uuid.uuid4(), firm_id=mine.id, agent_key="client_care", status=AgentRunStatus.COMPLETED,
                                 tier="tier_1", trigger="test", context={})
            s.add(other_run)
            await s.flush()
            other = Recommendation(id=uuid.uuid4(), firm_id=mine.id, run_id=other_run.id, agent_key="client_care", tier="tier_1",
                                   status=RecommendationStatus.PROPOSED, title="x", summary="s", rationale="r", confidence=0.8,
                                   priority=3, payload={}, evidence={}, citations=[])
            s.add(other)
            await s.commit()
            check("another agent's proposal", await ex().preview_change("decide_recommendation", {"recommendation_id": str(other.id), "action": "approve"}), None)

            print("\n=== the page's routes ===")
            listed = await engage.list_reports(firm=mine, db=s)
            check("the page lists this firm's", len(listed), (await ex().execute("read_reports", {}))["total"])
            check("the same fields as the tool", set(listed[0]), set((await ex().execute("read_reports", {}))["reports"][0]))
            page = await engage.get_report(seeded_draft.id, firm=mine, db=s)
            check("one report has its text and client", (page["sections"][0]["heading"], page["household_name"]), ("Summary", lee.name))
            try:
                await engage.get_report(theirs_report.id, firm=mine, db=s)
                check("the page cannot reach another firm's report", "read", 404)
            except HTTPException as exc:
                check("the page cannot reach another firm's report", exc.status_code, 404)
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
