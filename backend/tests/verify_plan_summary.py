"""Prove the drafted plan summary (B-196): written from the plan of record's frozen figures alone, and published only
through a proposal the client's adviser approves.

  - no summary is drafted from a plan that may not go to the client: no baseline, waiting for its review, or sent back;
  - once reviewed, the agent drafts one report of kind plan_summary, proposed whatever its tier, carrying the baseline;
  - its words are the baseline's frozen figures: a change to the live file after the baseline moves none of them, and a
    rerun rewrites the waiting draft rather than raising another; a portfolio review keeps its own draft;
  - an adviser approves, and the household's recorded adviser when it has one: a paraplanner, compliance and another
    adviser are refused, on the Confirm card and on the decision itself; compliance may dismiss;
  - approving publishes it, and records no delivery; rolling it back withdraws it;
  - a summary of a baseline that is no longer current is not published, on the card or at approval;
  - the plan of record names its summary, and when one may be drafted; firm scope.

Runs against a throwaway SQLite database.

    python -m tests.verify_plan_summary
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.atlas import runtime
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, plan_record, plan_summary, reports
from app.core import agent_runs, decision_rights
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import ClientReport
from app.models.enums import AgentKey, MarketType, RecommendationStatus, UserRole as R
from app.models.governance import Recommendation
from app.models.graph import Account, Goal, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.plan_record import PlanRecordEvent
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
ROOT = Path(__file__).resolve().parents[2]
KEY = AgentKey.PLAN_SUMMARY


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, decision_rights.DecisionForbidden, plan_record.PlanRecordError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:150]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def catalogue() -> None:
    print("\n=== The agent, its controls and where it is named ===")
    check("it stops at a proposal whatever its tier", agent_runs.forced_to_proposal(KEY), True)
    check("  it runs on one household", agent_runs.SUBJECT_REQUIRED.get(KEY), "household")
    check("only an adviser approves, modifies or revises", [sorted(r.value for r in decision_rights.roles_for(KEY, d)) for d in ("approve", "modify", "revise")],
          [["adviser"]] * 3)
    check("  an adviser or compliance dismisses or rolls back", [sorted(r.value for r in decision_rights.roles_for(KEY, d)) for d in ("dismiss", "rollback")],
          [["adviser", "compliance"]] * 2)
    check("  compliance is not chased to decide it, nor is an admin", (decision_rights.queue_action(R.COMPLIANCE, KEY), decision_rights.queue_action(R.ADMIN, KEY),
                                                                       decision_rights.queue_action(R.ADVISER, KEY)), (None, None, "decide"))
    for role in (R.PARAPLANNER, R.COMPLIANCE, R.PORTFOLIO_TEAM, R.BRANCH_LEADER, R.ADMIN):
        try:
            decision_rights.check(role, KEY, "approve")
            got = "allowed"
        except decision_rights.DecisionForbidden as e:
            got = "the client's adviser" in str(e)
        check(f"  {role.value} may not approve, and is told who may", got, True)
    check("it is one of the report agents", KEY.value in reports.REPORT_AGENTS, True)
    module = (ROOT / "backend" / "app" / "aurea_core" / "plan_summary.py").read_text(encoding="utf-8")
    agent = (ROOT / "backend" / "app" / "agents" / "plan_summary.py").read_text(encoding="utf-8")
    check("no model writes it: no narrate or gateway call", [w for w in ("narrate", "gateway", "llm") if w in agent or w in module.replace("no model", "")], [])
    check("  and it reads no live figure: no brain, engine or likelihood", [w for w in ("household_brain", "figures_of", "likelihoods", "financial_plan")
                                                                             if w in module or w in agent], [])
    backfill = (ROOT / "backend" / "seed" / "ai_governance_backfill.py").read_text(encoding="utf-8")
    check("the agent is on the AI inventory backfill", '("plan_summary"' in backfill, True)
    plan_view = (ROOT / "frontend" / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
    check("the plan of record offers the draft as a sentence to the conversation", "plan-record-summary" in plan_view and "Draft the plan summary" in plan_view, True)
    report_view = (ROOT / "frontend" / "components" / "views" / "ReportView.tsx").read_text(encoding="utf-8")
    check("  and a summary's redraft runs the plan summary, not a portfolio review", '"plan_summary"' in report_view, True)


async def db_checks() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Oak", slug="oak", base_currency="USD", jurisdiction="US", settings={})
            other = Firm(id=uuid.uuid4(), name="Kauri", slug="kauri", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)

            def user(f, name, role):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name.split()[0].lower()}@x.test", full_name=name, role=role, hashed_password="x", is_active=True)
                s.add(u)
                return u
            ann, ada, pat, cora, pete = (user(firm, "Ann Adviser", R.ADVISER), user(firm, "Ada Adviser", R.ADVISER),
                                         user(firm, "Pat Para", R.PARAPLANNER), user(firm, "Cora Compliance", R.COMPLIANCE),
                                         user(firm, "Pete Portfolio", R.PORTFOLIO_TEAM))
            kiri = user(other, "Kiri Adviser", R.ADVISER)
            park, lone = (Household(id=uuid.uuid4(), firm_id=firm.id, name=n) for n in ("Park", "Lone"))
            s.add_all([park, lone])
            await s.flush()
            inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="XEQ", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add(inst)
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=100, currency="USD", source="test", is_real=True))
            goals = {}
            for h, name in ((park, "Jin Park"), (lone, "Leo Lone")):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=name, date_of_birth=date(today.year - 55, 3, 1),
                           profile={"tax": {"annual_income": 150_000}})
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name="M", mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="A", currency="USD", cash_balance=0)
                s.add(a)
                await s.flush()
                s.add(Holding(firm_id=firm.id, account_id=a.id, instrument_id=inst.id, quantity=10_000, market_value=1_000_000, cost_basis=1_000_000))
                g = Goal(firm_id=firm.id, household_id=h.id, name="Retirement", kind="retirement", target_amount=1_500_000,
                         assumptions={"retirement_age": 65, "funding_share": 0.7, "annual_income": 70_000, "longevity_age": 95})
                s.add(g)
                goals[h.name] = g
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="household", to_id=park.id))
            await s.commit()

            def ex(u):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def run(h):
                await runtime.run_agent(s, firm=firm, agent_key=KEY, subject=Subject("household", h.id, h.name), trigger="manual",
                                        require_human=agent_runs.forced_to_proposal(KEY))
                await s.flush()

            async def open_recs(h):
                return (await s.execute(select(Recommendation).where(Recommendation.agent_key == KEY.value, Recommendation.subject_id == h.id,
                                                                     Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()

            async def summaries(h):
                return [r for r in (await s.execute(select(ClientReport).where(ClientReport.household_id == h.id))).scalars().all() if plan_summary.is_summary(r)]

            async def card(u, rec, action="approve") -> dict:
                return {r["label"]: r["value"] for r in (await ex(u).preview_change("decide_recommendation", {"recommendation_id": str(rec.id), "action": action}))["rows"]}

            P = {"household_id": str(park.id)}

            print("\n=== Nothing is drafted from a plan that may not go to the client ===")
            got = await ex(ann).read_plan_record(P)
            check("no baseline: none may be drafted, and it says why", (got["summary_ready"]["ok"], "no plan baseline" in got["summary_ready"]["why"]), (False, True))
            await run(park)
            check("  a run raises nothing and drafts nothing", (len(await open_recs(park)), len(await summaries(park))), (0, 0))
            await ex(pat).record_plan_baseline(P)
            got = await ex(ann).read_plan_record(P)
            check("waiting for its review: none may be drafted", (got["state"], got["summary_ready"]["ok"], "waiting for its review" in got["summary_ready"]["why"]),
                  ("baseline", False, True))
            await run(park)
            check("  a run drafts nothing", (len(await open_recs(park)), len(await summaries(park))), (0, 0))
            await ex(ann).record_plan_review({**P, "outcome": "failed", "findings": "Income need is out of date; check it."})
            got = await ex(ann).read_plan_record(P)
            check("sent back: none may be drafted", (got["summary_ready"]["ok"], "sent back" in got["summary_ready"]["why"]), (False, True))
            await ex(pat).record_plan_baseline(P)
            await ex(ann).record_plan_review({**P, "outcome": "passed"})
            got = await ex(ann).read_plan_record(P)
            check("reviewed: one may be drafted", (got["state"], got["summary_ready"]), ("reviewed", {"ok": True, "why": None}))
            check("  and there is none yet", got["summary"], None)
            frozen = got["baseline"]["figures"]
            baseline_id = got["baseline"]["id"]

            print("\n=== The agent drafts one summary, proposed, from the frozen figures ===")
            await run(park)
            recs, docs = await open_recs(park), await summaries(park)
            check("one open proposal and one draft report", (len(recs), len(docs)), (1, 1))
            rec, doc = recs[0], docs[0]
            check("  the report is a draft of kind plan_summary", (str(doc.status), doc.data["kind"]), ("draft", "plan_summary"))
            check("  it names the baseline and plan version it was drafted from", (doc.data["baseline_id"], doc.data["plan_version"], rec.payload["baseline_id"]),
                  (baseline_id, 1, baseline_id))
            check("  its figures are the baseline's frozen figures, as they are", doc.data["figures"], frozen)
            check("  the proposal carries the report and the recorded adviser", (rec.payload["report_id"], rec.payload["adviser_id"]), (str(doc.id), str(ann.id)))
            check("  its title and period", (doc.title, doc.period), ("Plan summary — Park", f"Plan v1 · figures as at {frozen['as_of']}"))
            heads = [x["heading"] for x in doc.sections]
            check("  sections from what the baseline holds", heads[0], "Your plan")
            check("  retirement, goals and the assumptions are each set out", all(h in heads for h in ("Retirement", "Your goals", "The assumptions")), True)
            text = " ".join(x["body"] for x in doc.sections)
            r = frozen["retirement"]
            check("  the retirement success is the frozen one", f"{r['success_probability']:.0%} of the market paths" in text, True)
            check("  each goal's likelihood is the frozen one", all(f"{g['name']}: {g['success_probability']:.0%} likely" in text for g in frozen["goals"]), True)
            check("  it names the assumption version", f"assumptions version {frozen['assumption_version']}" in text, True)
            check("  and says it is an estimate", "estimate" in text and "not a promise" in text, True)
            check("  it names no person", any(n in text for n in ("Ann", "Pat", "Ada")), False)
            check("  every number in it comes from the plan of record", _stray_numbers(text, got), [])

            print("\n=== A change to the live file moves nothing; a rerun rewrites the draft ===")
            before = [dict(x) for x in doc.sections]
            goals["Park"].target_amount = 9_000_000
            goals["Park"].assumptions = {**goals["Park"].assumptions, "annual_income": 400_000}
            await s.flush()
            await run(park)
            recs, docs = await open_recs(park), await summaries(park)
            check("still one open proposal and one draft", (len(recs), len(docs)), (1, 1))
            check("  the same draft, rewritten", docs[0].id, doc.id)
            check("  word for word what it was: the live change reached none of it", docs[0].sections, before)
            rec = recs[0]
            check("a portfolio review keeps its own draft: none waiting for it", await reports.waiting_draft(s, firm.id, park.id), None)
            check("  the plan summary's draft is found by its own agent", (await reports.waiting_draft(s, firm.id, park.id, agent=KEY.value)).id, doc.id)
            view = (await reports.views(s, firm.id, [doc]))[0]
            check("  the reports list reads its kind and that it awaits a decision", (view["kind"], view["awaiting_decision"]), ("plan_summary", True))

            print("\n=== Only the client's adviser approves ===")
            rows = await card(ann, rec)
            check("the Confirm card names the plan, its version and when its figures were frozen",
                  (rows.get("Plan summary"), rows.get("Plan"), rows.get("Figures frozen on")), ("Park", "v1", frozen["as_of"]))
            check("  and that approving records no delivery", "no delivery is recorded" in rows.get("Decision", ""), True)
            await refused("a paraplanner is refused on the card", card(pat, rec), "client's adviser")
            await refused("compliance is refused approval on the card", card(cora, rec), "client's adviser")
            await refused("another adviser is refused on the card, naming whose client it is", card(ada, rec), "ann adviser's client")
            await refused("  and on the decision itself", ex(ada).decide_recommendation({"recommendation_id": str(rec.id), "action": "approve"}), "ann adviser")
            check("compliance may dismiss: its card is offered", (await card(cora, rec, "dismiss")).get("Decision"), "Dismiss")
            check("nothing was published by any of that", (str(doc.status), doc.published_at), ("draft", None))
            await refused("another firm's adviser cannot reach it", ex(kiri).decide_recommendation({"recommendation_id": str(rec.id), "action": "approve"}), "not found")

            print("\n=== Approving publishes it, and records no delivery; rolling back withdraws it ===")
            deliveries = (await s.execute(select(func.count()).select_from(PlanRecordEvent).where(PlanRecordEvent.kind == "delivery"))).scalar_one()
            await ex(ann).decide_recommendation({"recommendation_id": str(rec.id), "action": "approve"})
            await s.refresh(doc)
            await s.refresh(rec)
            check("the client can read it", (str(doc.status), doc.published_at is not None, str(rec.status)), ("client_ready", True, "executed"))
            check("  no delivery was recorded", (await s.execute(select(func.count()).select_from(PlanRecordEvent).where(PlanRecordEvent.kind == "delivery"))).scalar_one(), deliveries)
            got = await ex(ann).read_plan_record(P)
            check("the plan of record names it as with the client", (got["summary"]["report_id"], got["summary"]["with_client"], got["summary"]["baseline_id"]),
                  (str(doc.id), True, baseline_id))
            check("  the reports list says it can be withdrawn", (await reports.views(s, firm.id, [doc]))[0]["can_withdraw"], True)
            await ex(ann).withdraw_report({"report_id": str(doc.id)})
            await s.refresh(doc)
            check("withdrawn: back to draft", (str(doc.status), doc.published_at), ("draft", None))

            print("\n=== A summary of a baseline that is no longer current is not published ===")
            await run(park)
            rec2 = (await open_recs(park))[0]
            doc2 = await reports.of_proposal(s, firm.id, rec2.payload)
            check("a fresh draft for the same baseline", (doc2.data["baseline_id"], str(doc2.status)), (baseline_id, "draft"))
            await ex(pat).record_plan_baseline(P)   # revises plan v1 before it is delivered
            got = await ex(ann).read_plan_record(P)
            check("the plan has a newer baseline waiting for review", (got["state"], got["summary_ready"]["ok"]), ("baseline", False))
            await refused("the Confirm card refuses to publish it", card(ann, rec2), "not published", "waiting for its review")
            agent = runtime.get_agent(KEY)
            rec2.decided_by = ann.id
            result = await agent.act(AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={},
                                                          subject=Subject("household", park.id, park.name)), rec2)
            check("  and at approval it publishes nothing, saying why", (result["executed"], "not published" in result["note"].lower()), (False, True))
            await s.refresh(doc2)
            check("  the draft stays a draft", str(doc2.status), "draft")
            await ex(ada).record_plan_review({**P, "outcome": "passed"})
            got = await ex(ann).read_plan_record(P)
            why = await plan_summary.still_current(s, firm.id, doc2)
            check("once the newer baseline passes, the old draft is still refused: it names the newer version",
                  (got["summary_ready"]["ok"], "has a newer baseline" in (why or "")), (True, True))

            print("\n=== A household with no adviser on record: any adviser approves ===")
            L = {"household_id": str(lone.id)}
            await ex(pat).record_plan_baseline(L)
            await ex(ada).record_plan_review({**L, "outcome": "passed"})
            await run(lone)
            lrec = (await open_recs(lone))[0]
            check("the proposal names no adviser", lrec.payload["adviser_id"], None)
            check("  any adviser's card is offered", (await card(ada, lrec)).get("Plan summary"), "Lone")
            await refused("  a paraplanner's is not", card(pat, lrec), "client's adviser")
            check("firm scope: another firm reads no plan summary of this one", await plan_summary.latest(s, other.id, park.id), None)

            print("\n=== Healthcare (B-191): it says whether the figures include it, from what the baseline froze ===")
            ldoc = (await summaries(lone))[0]
            ltext = " ".join(x["body"] for x in ldoc.sections)
            check("no cost stated by the firm: the baseline froze healthcare as not assessed", ldoc.data["figures"]["retirement"]["healthcare"]["status"], "not_assessed")
            check("  and the summary says it is not in the figures", "Healthcare costs are not included in these figures." in ltext, True)
            check("a baseline frozen before healthcare was laid out says nothing of it", plan_summary._care_sentences(None, str), [])
            from app.aurea_core import assumptions

            await assumptions.record(s, firm, pete, reason="Healthcare costs from the 2026 survey", changes=[
                {"figure": "healthcare.pre65", "value": 15_000, "source": "Test survey of individual premiums, 2026"},
                {"figure": "healthcare.medicare", "value": 4_000, "source": "Test survey of Medigap and Part D, 2026"}])
            await s.commit()
            await ex(pat).record_plan_baseline(L)   # revises plan v1 before it is delivered
            await ex(ada).record_plan_review({**L, "outcome": "passed"})
            await run(lone)
            ldoc = (await summaries(lone))[0]
            ltext = " ".join(x["body"] for x in ldoc.sections)
            care = ldoc.data["figures"]["retirement"]["healthcare"]
            check("once the firm states a cost, the new baseline froze it as funded", (care["status"] in ("assessed", "partly_assessed"), care["first_year_today"] is not None), (True, True))
            check("  and the summary says the retirement figures include it, with the frozen amounts",
                  ("funds healthcare" in ltext, "the likelihood above includes it" in ltext, f"{care['total_today']:,.0f}" in ltext), (True, True, True))
            check("  and no longer says it is left out", "are not included in these figures" in ltext, False)
            check("  every number in it still comes from the plan of record", _stray_numbers(ltext, await ex(ada).read_plan_record(L)), [])
            check("  the retirement figures a baseline froze keep their names", sorted(k for k in ldoc.data["figures"]["retirement"] if k != "healthcare"),
                  sorted(["success_probability", "on_track", "band", "sustainable_income", "income_need", "income_target",
                          "projected_at_retirement_today", "retirement_age", "longevity_age"]))
    finally:
        await db.dispose()


def _stray_numbers(text: str, record: dict) -> list[str]:
    """Every number in the text that is not one of the plan of record's own: its version, its dates, its frozen
    amounts, ages and shares, and the funded ratio's fixed 1.00."""
    fig = record["baseline"]["figures"]
    text = text.replace(str(fig.get("assumption_version")), "")
    allowed = {str(record["version"]), "1.00"}
    for day in (record["baseline"]["happened_on"], (record.get("review") or {}).get("happened_on"), fig.get("as_of")):
        allowed |= set(str(day or "").split("-"))
    allowed |= {f"{float(x):.0f}" for x in _amounts(fig)}
    allowed |= ({f"{float(x) * 100:.0f}" for x in _shares(fig)} | {f"{float(x) * 100:.1f}" for x in _shares(fig)}
                | {f"{float(x):.2f}" for x in _shares(fig)})
    found = {n.strip(",.").replace(",", "") for n in re.findall(r"\d[\d,.]*", text)}
    return sorted(n for n in found if n not in allowed)


def _amounts(fig: dict) -> list:
    r, f = fig.get("retirement") or {}, fig.get("funded") or {}
    care = r.get("healthcare") or {}
    return [v for v in (care.get("first_year_today"), care.get("total_today"), r.get("income_need"), r.get("sustainable_income"), r.get("projected_at_retirement_today"), f.get("assets"), f.get("pv_needs"),
                        r.get("retirement_age"), r.get("longevity_age")) if v is not None]


def _shares(fig: dict) -> list:
    r, f = fig.get("retirement") or {}, fig.get("funded") or {}
    return [v for v in (r.get("success_probability"), f.get("ratio"), f.get("discount_rate"), *[g.get("success_probability") for g in fig.get("goals") or []])
            if v is not None]


async def main() -> int:
    catalogue()
    await db_checks()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
