"""Prove the plan of record (B-181): a household's plan as a produced artifact, its state worked out from evidence.

  - the state walks from not_started to complete on what is recorded, and is never set;
  - a baseline is refused while a fact the plan reads is missing, and the missing facts are listed;
  - a second person reviews: whoever recorded the baseline is refused; a failed review needs findings and sends the
    plan back for a new baseline;
  - a delivery is refused until a needed review has passed; complexity is counted part by part, and until the firm sets
    its line every plan is reviewed, named as the default;
  - a delivery keeps the baseline's frozen figures, which do not move when the file changes after it; each action item
    becomes a task for the recorded adviser, and the state reads those tasks;
  - a baseline after a delivery starts the next plan version;
  - freshness is read against the household's review cadence, else twelve months named as assumed;
  - the firm's coverage, freshness, QC pass rate and scenario depth, with concrete numbers; an adviser counts their own;
  - rights (a client and operations read nothing; only an adviser or paraplanner prepares; the portfolio team reviews);
  - firm scope; and each Confirm card names the change, refuses what recording would refuse, and writes nothing.

Runs against a throwaway SQLite database.

    python -m tests.verify_plan_record
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, plan_record, planning
from app.core.db import Base
from app.core.tools import CLIENT_TOOLS, ONE_CLIENT_VIEWS, OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import MarketType, TaskStatus, UserRole as R
from app.models.governance import LedgerEntry
from app.models.graph import Account, Goal, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.plan_record import PlanRecordEvent
from app.models.portfolio import Holding, Instrument, Price
from app.models.scenarios import PlanScenario
from app.models.tenant import Firm

PASS, FAIL = [], []
ROOT = Path(__file__).resolve().parents[2]
TOOL_KEYS = ("read_plan_record", "record_plan_baseline", "record_plan_review", "record_plan_delivery")
READERS = {R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM, R.COMPLIANCE, R.BRANCH_LEADER}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, plan_record.PlanRecordError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:140]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def catalogue() -> None:
    print("\n=== The tools, and who holds them ===")
    check("read_plan_record changes nothing", TOOLS["read_plan_record"].change_state, ToolChangeState.NO)
    check("  read by advisers, paraplanners, the portfolio team, compliance and branch leaders", TOOLS["read_plan_record"].roles_required, READERS)
    for key, roles in (("record_plan_baseline", {R.ADVISER, R.PARAPLANNER}), ("record_plan_review", {R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM}),
                       ("record_plan_delivery", {R.ADVISER, R.PARAPLANNER})):
        check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check(f"  held by {sorted(r.value for r in roles)}", TOOLS[key].roles_required, roles)
    check("a client holds none of them", [k for k in TOOL_KEYS if k in CLIENT_TOOLS or k in tools_for_role(R.CLIENT)], [])
    check("operations holds none of them", [k for k in TOOL_KEYS if k in tools_for_role(R.OPERATIONS)], [])
    module = (ROOT / "backend" / "app" / "aurea_core" / "plan_record.py").read_text(encoding="utf-8")
    check("nothing in the module edits or deletes a record", any(w in module for w in ("session.delete", "setattr(", "flag_modified")), False)
    check("  and it writes to no goal, mandate, plan figure or the advice file",
          [w for w in ("Goal(", "Mandate(", "IncomeStream(", "AdviceRecommendation", "advice_file") if w in module], [])
    backfill = (ROOT / "backend" / "seed" / "ai_governance_backfill.py").read_text(encoding="utf-8")
    check("every tool is on the AI inventory backfill", [k for k in TOOL_KEYS if f'("{k}"' not in backfill], [])

    print("\n=== The views: the plan tab, the Advice checks section and the work pane ===")
    check("open_view opens the firm's plans, gated like its page", OPEN_VIEWS.get("plans"), ("/studio/plans", "Plans of record", READERS))
    check("  a firm-wide view, not one client's", "plans" in ONE_CLIENT_VIEWS, False)
    nav = (ROOT / "frontend" / "lib" / "nav.ts").read_text(encoding="utf-8")
    advice = nav.split('{ key: "advice"', 1)[1].split("] },", 1)[0]
    check("it is a page of the Advice checks section", '"/studio/plans"' in advice, True)
    check("  not a new top-level entry: no SECTION of its own", nav.count('key: "plans"'), 0)
    panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    work = (ROOT / "frontend" / "app" / "workspace" / "components" / "WorkPane.tsx").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
    check("it is a pane view", 'key: "plans"' in panes and 'case "plans":' in work, True)
    check("each tool's result draws a card and opens a view", [k for k in TOOL_KEYS if f'case "{k}":' not in renderer or f'case "{k}":' not in panes], [])
    page = ROOT / "frontend" / "app" / "(app)" / "studio" / "plans" / "page.tsx"
    check("the page is the view, not a copy", page.exists() and "PlansView" in page.read_text(encoding="utf-8"), True)
    plan_view = (ROOT / "frontend" / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
    check("one household's record is a section of its Plan tab", "PlanRecordSection" in plan_view, True)


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
            ann, ada, pat, pete, cora, bea, opal, cleo = (
                user(firm, "Ann Adviser", R.ADVISER), user(firm, "Ada Adviser", R.ADVISER), user(firm, "Pat Para", R.PARAPLANNER),
                user(firm, "Pete Portfolio", R.PORTFOLIO_TEAM), user(firm, "Cora Compliance", R.COMPLIANCE), user(firm, "Bea Branch", R.BRANCH_LEADER),
                user(firm, "Opal Ops", R.OPERATIONS), user(firm, "Cleo Client", R.CLIENT))
            kiri = user(other, "Kiri Adviser", R.ADVISER)
            park, lone, bare, half = (Household(id=uuid.uuid4(), firm_id=firm.id, name=n) for n in ("Park", "Lone", "Bare", "Half"))
            gone = Household(id=uuid.uuid4(), firm_id=firm.id, name="Gone", left_on=today - timedelta(days=5))
            s.add_all([park, lone, bare, half, gone])
            await s.flush()
            inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="XEQ", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add(inst)
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=100, currency="USD", source="test", is_real=True))

            async def client(h, name, *, plan_to=True):
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
                         assumptions={"retirement_age": 65, "funding_share": 0.7, "annual_income": 70_000, **({"longevity_age": 95} if plan_to else {})})
                s.add(g)
                await s.flush()
                return p, g
            jin, retire = await client(park, "Jin Park", plan_to=False)
            await client(lone, "Leo Lone")
            s.add(Person(id=uuid.uuid4(), firm_id=firm.id, household_id=half.id, full_name="Hal Half", date_of_birth=date(today.year - 40, 1, 1)))
            s.add(Person(id=uuid.uuid4(), firm_id=firm.id, household_id=gone.id, full_name="Gil Gone", date_of_birth=date(today.year - 70, 1, 1),
                         profile={"tax": {"annual_income": 1}}))
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="household", to_id=park.id))
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ada.id, to_type="household", to_id=lone.id))
            await s.commit()

            def ex(u):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def count(model) -> int:
                return (await s.execute(select(func.count()).select_from(model))).scalar_one()

            async def ledgered(kind) -> int:
                return (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == kind))).scalar_one()

            async def state(h) -> str:
                return (await ex(cora).read_plan_record({"household_id": str(h.id)}))["state"]

            async def card(u, tool, inputs) -> dict:
                return {r["label"]: r["value"] for r in (await ex(u).preview_change(tool, inputs))["rows"]}

            async def scenario(h, name):
                s.add(PlanScenario(firm_id=firm.id, household_id=h.id, name=name, levers={"retire_at": 67}, result={}, made_by=pat.id, made_by_name="Pat Para"))
                await s.flush()

            P = {"household_id": str(park.id)}

            print("\n=== Before a baseline: the state is the data's ===")
            check("nothing on file is not started", await state(bare), "not_started")
            check("some facts on file, not all, is data incomplete", await state(half), "data_incomplete")
            got = await ex(pat).read_plan_record(P)
            check("Park is missing its plan-to age, named", (got["state"], [m["text"] for m in got["data"]["missing"]]), ("data_incomplete", ["plan-to age"]))
            check("  and the owner is the paraplanner", got["owner"], "Paraplanner")
            await refused("a baseline is refused while facts are missing, and lists them", ex(pat).record_plan_baseline(P), "not on file", "plan-to age")
            await refused("  the Confirm card refuses it too", ex(pat).preview_change("record_plan_baseline", P), "plan-to age")
            retire.assumptions = {**retire.assumptions, "longevity_age": 95}
            await s.flush()
            check("with the fact recorded, data complete", await state(park), "data_complete")

            print("\n=== Complexity, counted from what is on file, and the default line ===")
            c = got["complexity"]
            check("parts counted one by one", {p["part"]: p["count"] for p in c["parts"]},
                  {"entities": 0, "accounts": 1, "goals": 1, "income_streams": 0, "debts": 0, "held_away_lines": 0, "equity_grants": 0, "policies": 0})
            check("  the score is their sum", c["score"], 2)
            need = got["review_need"]
            check("with no line set every plan is reviewed, named as the default", (need["needed"], need["basis"], need["line_assumed"]), (True, "default", True))

            print("\n=== The baseline's Confirm card names what it freezes, and writes nothing ===")
            rows = await card(pat, "record_plan_baseline", P)
            check("it names the client and the version", (rows.get("Client"), rows.get("Plan")), ("Park", "v1, the first"))
            check("  the assumption version it freezes", rows.get("Assumption version"), planning.assumption_version(firm.settings))
            check("  the headline figures", all(k in rows for k in ("Retirement success", "Sustainable income", "Funded ratio", "Goals")), True)
            check("  that a review is needed, by default", rows.get("Review"), "needed: every plan is reviewed until the firm sets its complexity line")
            check("  and that it changes no plan figure", "changes no goal, mandate or plan figure" in rows.get("Changes", ""), True)
            check("showing the card wrote nothing", await count(PlanRecordEvent), 0)
            await refused("compliance is offered no baseline card", ex(cora).preview_change("record_plan_baseline", P), "adviser or a paraplanner")

            print("\n=== Baseline, scenarios, review: the state follows the evidence ===")
            goals_before = [(g.name, float(g.target_amount)) for g in (await s.execute(select(Goal).order_by(Goal.name))).scalars().all()]
            first = await ex(pat).record_plan_baseline(P)
            check("baselined: v1, the paraplanner's to work", (first["state"], first["version"], first["owner"]), ("baseline", 1, "Paraplanner"))
            frozen1 = first["baseline"]["figures"]
            check("  it froze the retirement success and the assumption version",
                  (frozen1["retirement"]["success_probability"] is not None, frozen1["assumption_version"]), (True, planning.assumption_version(firm.settings)))
            check("  each goal's likelihood and tier, the needs, the funded ratio and the inputs",
                  ([g["name"] for g in frozen1["goals"]], "success" in frozen1["tiers"], "ratio" in frozen1["funded"], frozen1["inputs"]["completeness"]["share"]),
                  (["Retirement"], True, True, 1.0))
            check("  the ledger has it", await ledgered("plan_baseline_recorded"), 1)
            await refused("a delivery is refused before a needed review", ex(ann).record_plan_delivery({**P, "evidence_kind": "note", "evidence": "Meeting note 12 May"}),
                          "needs a review by a second person", "every plan is reviewed")
            await scenario(park, "Retire at 67")
            await scenario(park, "Spend less")
            got = await ex(cora).read_plan_record(P)
            check("two scenarios saved since, and a review needed: in review, the reviewer's", (got["state"], got["scenarios_since_baseline"], got["owner"]),
                  ("in_review", 2, "Reviewer"))

            print("\n=== A second person reviews ===")
            await refused("whoever recorded the baseline does not review it", ex(pat).record_plan_review({**P, "outcome": "passed"}), "second person")
            await refused("  nor on the card", ex(pat).preview_change("record_plan_review", {**P, "outcome": "passed"}), "second person")
            await refused("compliance does not review a plan", ex(cora).record_plan_review({**P, "outcome": "passed"}), "reviewed by an adviser")
            await refused("  and the core module refuses it too, not only the tool",
                          plan_record.record_review(s, firm.id, role="compliance", recorded_by=cora.email, user_id=cora.id, household_id=str(park.id), outcome="passed"),
                          "reviewed by an adviser")
            await refused("operations does not review a plan", ex(opal).record_plan_review({**P, "outcome": "passed"}), "reviewed by an adviser")
            await refused("a fail names its findings", ex(ann).record_plan_review({**P, "outcome": "failed"}), "findings")
            await refused("an outcome is passed or failed", ex(ann).record_plan_review({**P, "outcome": "fine"}), "passed or failed")
            rows = await card(ann, "record_plan_review", {**P, "outcome": "failed", "findings": "Spending ignores the mortgage payments"})
            check("the review card names the baseline, the outcome and the reviewer",
                  (rows.get("Baseline", "").startswith("v1"), rows.get("Outcome"), rows.get("Reviewer")), (True, "Failed: sent back for a new baseline", ann.email))
            failed = await ex(ann).record_plan_review({**P, "outcome": "failed", "findings": "Spending ignores the mortgage payments"})
            check("a failed review sends the plan back to its data", (failed["state"], failed["sent_back"]["findings"]),
                  ("data_complete", "Spending ignores the mortgage payments"))
            await refused("  it cannot be delivered", ex(ann).record_plan_delivery({**P, "evidence_kind": "note", "evidence": "Meeting note 12 May"}), "new baseline")
            await refused("  nor reviewed again", ex(pete).record_plan_review({**P, "outcome": "passed"}), "sent back")
            second = await ex(pat).record_plan_baseline(P)
            check("a new baseline revises v1", (second["state"], second["version"], second["review"]), ("baseline", 1, None))
            await scenario(park, "Claim later")
            check("  a scenario after it, and it is in review again", await state(park), "in_review")
            passed = await ex(pete).record_plan_review({**P, "outcome": "passed"})
            check("the portfolio team passes it: reviewed, the adviser's to deliver", (passed["state"], passed["owner"]), ("reviewed", "Adviser"))
            check("  the ledger has both reviews", await ledgered("plan_review_recorded"), 2)
            await refused("a passed baseline is not reviewed twice", ex(ann).record_plan_review({**P, "outcome": "passed"}), "passed review")

            print("\n=== Delivery: from evidence, the figures frozen, the action items raised as tasks ===")
            due = (today + timedelta(days=30)).isoformat()
            delivery = {**P, "evidence_kind": "note", "evidence": "Meeting note: plan presented and agreed",
                        "action_items": [{"title": "Raise the 401(k) deferral to the maximum", "due_on": due}, "Update the IRA beneficiary"]}
            await refused("a delivery names its evidence", ex(ann).record_plan_delivery({**delivery, "evidence_kind": "email"}), "document or a meeting note")
            await refused("  with its words", ex(ann).record_plan_delivery({**delivery, "evidence": "ok"}), "name the note")
            await refused("  happened, not planned", ex(ann).record_plan_delivery({**delivery, "delivered_on": (today + timedelta(days=1)).isoformat()}), "has happened")
            await refused("an action item due in the past", ex(ann).record_plan_delivery({**delivery, "action_items": [{"title": "Late", "due_on": (today - timedelta(days=1)).isoformat()}]}),
                          "due today or later")
            await refused("the portfolio team does not deliver", ex(pete).record_plan_delivery(delivery), "adviser or a paraplanner")
            rows = await card(ann, "record_plan_delivery", delivery)
            check("the delivery card names the review, the evidence and the tasks",
                  (rows.get("Review", "").startswith("passed"), rows.get("Evidence"), rows.get("Tasks for")),
                  (True, "Note: Meeting note: plan presented and agreed", "Ann Adviser"))
            check("  the figures it freezes, with the assumption version", f"assumptions {planning.assumption_version(firm.settings)}" in rows.get("Figures frozen", ""), True)
            check("  and the action items", rows.get("Action items"), f"Raise the 401(k) deferral to the maximum (due {due}); Update the IRA beneficiary")
            check("showing it wrote nothing", (await count(Task), await count(PlanRecordEvent)), (0, 4))
            done = await ex(ann).record_plan_delivery(delivery)
            p = done["plan_of_record"]
            check("delivered with open tasks: implementing", (done["state"], done["open_tasks"], done["tasks"]), ("implementing", 2, 2))
            check("  it keeps the reviewed baseline's figures exactly", p["figures"], second["baseline"]["figures"])
            check("  each action item is a task for the recorded adviser, due on the firm's calendar",
                  sorted((t["title"], t["assigned_to"], t["due_date"]) for t in p["tasks"]),
                  [("Raise the 401(k) deferral to the maximum", "Ann Adviser", due), ("Update the IRA beneficiary", "Ann Adviser", None)])
            check("  the tasks are on the task list, from the plan", sorted((t.source, t.household_id == park.id) for t in (await s.execute(select(Task))).scalars().all()),
                  [("plan_delivery", True), ("plan_delivery", True)])
            check("  the ledger has the delivery", await ledgered("plan_delivered"), 1)
            check("  nothing changed a goal", [(g.name, float(g.target_amount)) for g in (await s.execute(select(Goal).order_by(Goal.name))).scalars().all()], goals_before)
            f = done["freshness"]
            check("fresh against a cadence named as assumed: twelve months with none on file", (f["fresh"], f["cadence_months"], f["cadence_assumed"]), (True, 12, True))
            await refused("a delivered plan is not delivered twice", ex(ann).record_plan_delivery(delivery), "was delivered")
            await refused("  nor reviewed", ex(pete).record_plan_review({**P, "outcome": "passed"}), "was delivered")

            print("\n=== The file changes after delivery; the plan of record does not ===")
            s.add(Goal(firm_id=firm.id, household_id=park.id, name="Boat", kind="other", target_amount=80_000, target_date=date(today.year + 3, 6, 1), assumptions={}))
            await s.flush()
            now = await ex(cora).read_plan_record(P)
            check("the plan of record's figures are as frozen", now["plan_of_record"]["figures"], second["baseline"]["figures"])
            check("  today's figures sit beside them, and differ", ("Boat" in [g["name"] for g in now["today"]["goals"]], any(d["goal"] == "Boat" for d in now["differences"])),
                  (True, True))
            check("  and the frozen goals are the ones delivered", [g["name"] for g in now["plan_of_record"]["figures"]["goals"]], ["Retirement"])

            print("\n=== Closing the tasks completes the plan ===")
            tasks = (await s.execute(select(Task).order_by(Task.title))).scalars().all()
            tasks[0].status = TaskStatus.DONE
            await s.flush()
            check("one closed, one open: still implementing", await state(park), "implementing")
            tasks[1].status = TaskStatus.DISMISSED
            await s.flush()
            check("both closed: complete", await state(park), "complete")

            print("\n=== A baseline after a delivery starts the next plan ===")
            rows = await card(pat, "record_plan_baseline", P)
            check("the card says it is a new plan", rows.get("Plan"), "v2, a new plan after the delivered one")
            v2 = await ex(pat).record_plan_baseline(P)
            check("plan v2, baselined", (v2["state"], v2["version"]), ("baseline", 2))
            check("  the delivered v1 stays the plan of record", (v2["plan_of_record"]["version"], v2["plan_of_record"]["figures"]), (1, second["baseline"]["figures"]))
            check("  v2 froze the goal added since", sorted(g["name"] for g in v2["baseline"]["figures"]["goals"]), ["Boat", "Retirement"])
            check("  and the complexity it was baselined at", v2["baseline"]["complexity"]["score"], 3)

            print("\n=== Within the firm's line no review is needed; freshness reads the cadence ===")
            firm.settings = {**(firm.settings or {}), "plan_review_line": 5}
            lone.review_cadence_months = 6
            await s.flush()
            L = {"household_id": str(lone.id)}
            rows = await card(ada, "record_plan_baseline", L)
            check("the card says no review is needed within the line", rows.get("Review"), "not needed: within the firm's line of 5")
            lb = await ex(ada).record_plan_baseline(L)
            check("  baselined with the line frozen", (lb["review_need"]["needed"], lb["review_need"]["basis"], lb["review_need"]["line"]), (False, "within_line", 5))
            ld = await ex(ada).record_plan_delivery({**L, "evidence_kind": "document", "evidence": "Signed plan acknowledgement"})
            check("delivered with no review and no action items: delivered, nothing to carry out", (ld["state"], ld["tasks"]), ("delivered", 0))
            check("  fresh against its six-month cadence", (ld["freshness"]["fresh"], ld["freshness"]["cadence_months"], ld["freshness"]["cadence_assumed"]), (True, 6, False))
            # Time passes: the delivery and its baseline are seven months old.
            for e in (await s.execute(select(PlanRecordEvent).where(PlanRecordEvent.household_id == lone.id))).scalars().all():
                e.happened_on = today - timedelta(days=210)
            await s.flush()
            aged = (await ex(cora).read_plan_record(L))["freshness"]
            check("seven months on, stale against six", (aged["fresh"], aged["age_days"]), (False, 210))

            print("\n=== The firm's metrics ===")
            m = await ex(cora).read_plan_record({})
            check("coverage: two of four active households have a delivered plan (the one that left is not counted)",
                  (m["coverage"]["delivered"], m["coverage"]["active"], m["coverage"]["share"]), (2, 4, 0.5))
            check("freshness: one of two fresh, one on the assumed cadence", (m["freshness"]["fresh"], m["freshness"]["delivered"], m["freshness"]["share"], m["freshness"]["cadence_assumed"]),
                  (1, 2, 0.5, 1))
            check("QC: one passed of two reviews", (m["qc"]["passed"], m["qc"]["reviews"], m["qc"]["pass_rate"]), (1, 2, 0.5))
            check("scenario depth over four baselines: 2, 1, 0, 0", (m["scenario_depth"]["baselines"], m["scenario_depth"]["scenarios"],
                                                                     m["scenario_depth"]["median"], m["scenario_depth"]["mean"]), (4, 3, 0.5, 0.75))
            check("counts by state", {k: v for k, v in m["by_state"].items() if v}, {"not_started": 1, "data_incomplete": 1, "baseline": 1, "delivered": 1})
            check("  a row per household", sorted((r["household"], r["state"]) for r in m["households"]),
                  [("Bare", "not_started"), ("Half", "data_incomplete"), ("Lone", "delivered"), ("Park", "baseline")])
            none = await ex(cora).read_plan_record({"since": "2001-01-01", "until": "2001-12-31"})
            check("no review in a period is no pass rate, never zero", (none["qc"]["reviews"], none["qc"]["pass_rate"]), (0, None))
            await refused("a period backwards", ex(cora).read_plan_record({"since": today.isoformat(), "until": (today - timedelta(days=1)).isoformat()}), "on or before")
            mine = await ex(ann).read_plan_record({})
            check("an adviser counts their own households", (mine["scope"], mine["coverage"]["active"], mine["coverage"]["delivered"], [r["household"] for r in mine["households"]]),
                  ("own", 1, 1, ["Park"]))
            theirs = await ex(ada).read_plan_record({})
            check("  and another adviser theirs", ([r["household"] for r in theirs["households"]], theirs["freshness"]["fresh"], theirs["qc"]["reviews"]), (["Lone"], 0, 0))
            check("a branch leader reads the firm's", (await ex(bea).read_plan_record({}))["coverage"]["active"], 4)

            print("\n=== Rights ===")
            await refused("a client reads none of it", ex(cleo).read_plan_record(P), "read by advisers")
            await refused("operations reads none of it", ex(opal).read_plan_record({}), "read by advisers")
            await refused("  nor in the core module", plan_record.record_baseline(s, firm.id, role="operations", recorded_by=opal.email, user_id=opal.id,
                                                                                   household_id=str(park.id)), "adviser or a paraplanner")
            await refused("compliance records no baseline", ex(cora).record_plan_baseline(P), "adviser or a paraplanner")
            await refused("  nor a delivery", ex(cora).record_plan_delivery(delivery), "adviser or a paraplanner")
            await refused("the portfolio team records no baseline", ex(pete).record_plan_baseline(P), "adviser or a paraplanner")
            await refused("a client who has left gets no new plan", ex(ann).record_plan_baseline({"household_id": str(gone.id)}), "has left")

            print("\n=== Firm scope ===")
            await refused("another firm reads no household of ours", ex(kiri).read_plan_record(P), "not found")
            check("  its firm view holds none of ours", (await ex(kiri).read_plan_record({}))["households"], [])
            await refused("  records no baseline on ours", ex(kiri).record_plan_baseline(P), "not in this firm")
            await refused("  nor a review", ex(kiri).record_plan_review({**P, "outcome": "passed"}), "not in this firm")
            await refused("  nor a delivery", ex(kiri).record_plan_delivery(delivery), "not in this firm")
            check("every event is the firm's own, none edited away", (await count(PlanRecordEvent), {e.firm_id for e in (await s.execute(select(PlanRecordEvent))).scalars().all()}),
                  (8, {firm.id}))
    finally:
        await db.dispose()


def main() -> int:
    catalogue()
    asyncio.run(db_checks())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
