"""Protection slice 2: the agent that raises gaps, and long-term care.

    python -m tests.verify_protection_agent

Against a throwaway SQLite database, through the real runtime, with the model scripted. Checks that:

  - a measured gap becomes one proposal per household, kept up to date, and closed when it clears;
  - a household that cannot be assessed raises nothing, and another firm's households are not read;
  - approving adds a review task and nothing else, and rolling back takes it away;
  - a gap someone has decided on stays quiet until it grows or six months pass, and rolling back undoes that;
  - a person asking about one household is always told;
  - at Tier 3 nothing is added with nobody having decided;
  - care is worked from the firm's stated cost and no other figure, and with none it is "not assessed";
  - setting that cost is a firm leader's, names its source, goes on the ledger and shows on its Confirm card.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.agents import protection_planning as agent_mod
from app.agents.catalogue import CATALOGUE
from app.agents.schedules import DEFAULT_SCHEDULES
from app.atlas import runtime
from app.atlas.base import Subject
from app.atlas.registry import get_agent
from app.aurea_core import firm_time, protection as pr
from app.core import agent_runs
from app.core.db import Base, utcnow
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import AgentKey, HumanAction, RecommendationStatus, UserRole
from app.models.governance import LedgerEntry, Recommendation
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import AgentConfig, Firm
from seed.ai_governance_backfill import _AGENTS, _TOOLS

PASS, FAIL = [], []
R = UserRole
KEY = AgentKey.PROTECTION_PLANNING


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def gating() -> None:
    print("\n=== how it is wired ===")
    agent = get_agent(KEY)
    check("the agent is registered and scheduled", (agent.name, agent.scheduled), ("Protection Planner", True))
    check("it is in the catalogue", KEY in CATALOGUE, True)
    check("it has a cadence", KEY in DEFAULT_SCHEDULES, True)
    check("a scan closes what it no longer finds", agent.expires_unraised, True)
    check("it is in the AI inventory", "protection_planning" in [a[0] for a in _AGENTS], True)
    check("and so is the care tool", "set_care_assumptions" in [t[0] for t in _TOOLS], True)
    check("a run stops at a proposal whatever the tier", agent_runs.forced_to_proposal(KEY), True)
    check("  and never acts on its own", agent_runs.acts_on_its_own(KEY, "tier_3"), False)
    check("an adviser can start it", agent_runs.may_run(R.ADVISER, KEY), True)
    check("an administrator cannot", agent_runs.may_run(R.ADMIN, KEY), False)
    tool = TOOLS["set_care_assumptions"]
    check("setting care pauses", tool.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("and is a firm leader's", tool.roles_required, {R.BRANCH_LEADER, R.ADMIN})
    check("an adviser does not hold it", "set_care_assumptions" in tools_for_role(R.ADVISER), False)
    check("a client does not", "set_care_assumptions" in tools_for_role(R.CLIENT), False)
    check("the protection view opens beside the conversation", "protection" in OPEN_VIEWS, True)


def care_sums() -> None:
    print("\n=== the care sum ===")
    basis = {"annual_cost": 100_000, "years": 4}
    check("under the care age it is not yet the question", pr.assess_care(age=54, basis=basis, assets=0.0)["status"], "not_yet")
    check("no date of birth is not assessed", pr.assess_care(age=None, basis=basis, assets=0.0)["missing"], ["date_of_birth"])
    none = pr.assess_care(age=70, basis=None, assets=9_000_000.0)
    check("no cost of care is not assessed, and says why, however wealthy", (none["status"], none["missing"]), ("not_assessed", ["care_cost"]))
    check("  and carries no figure", "gap" in none, False)
    r = pr.assess_care(age=70, basis=basis, assets=100_000.0)
    check("the need is a year's care for the years planned", r["need"], 400_000.0)
    check("less the assets", (r["status"], r["gap"]), ("gap", 300_000.0))
    r = pr.assess_care(age=70, basis=basis, assets=0.0, benefits=[(60_000.0, 2)])
    check("a benefit paid for two years counts for two", (r["cover"], r["gap"]), (120_000.0, 280_000.0))
    r = pr.assess_care(age=70, basis=basis, assets=0.0, benefits=[(60_000.0, None)])
    check("one paid while care lasts counts for all of it", r["cover"], 240_000.0)
    r = pr.assess_care(age=70, basis=basis, assets=0.0, benefits=[(60_000.0, 9)])
    check("one longer than the care planned is capped at it", r["cover"], 240_000.0)
    r = pr.assess_care(age=70, basis=basis, assets=900_000.0)
    check("assets can close it", (r["status"], r["gap"]), ("no_gap", 0.0))
    check("and what they did stays visible", r["gap_before_assets"], 400_000.0)
    check("the firm's own care age is used", pr.assess_care(age=60, basis=basis, assets=0.0, from_age=65)["status"], "not_yet")


async def behaviour() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            mine = Firm(id=uuid.uuid4(), name="Mine", slug="mine", base_currency="NZD", settings={"pii_redaction": True})
            theirs = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs", base_currency="NZD")
            s.add_all([mine, theirs])
            await s.flush()
            today = firm_time.today(mine)

            def user(firm, name, role):
                return User(id=uuid.uuid4(), firm_id=firm.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=role, is_active=True)
            sophie = user(mine, "Sophie Tran", R.ADVISER)
            mark = user(mine, "Mark Sullivan", R.BRANCH_LEADER)
            s.add_all([sophie, mark])

            def household(name, firm=mine):
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(h)
                return h

            def person(h, name, income=None, age=45, firm=mine):
                profile = {"tax": {"annual_income": income}} if income is not None else {}
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=name,
                           date_of_birth=date(today.year - age, 1, 1), profile=profile)
                s.add(p)
                return p

            chen, unknown, older, far = household("Chen Family"), household("Unknown Family"), household("Older Family"), household("Far Family", theirs)
            await s.flush()
            wei = person(chen, "Wei", 200_000)
            mei = person(chen, "Mei", 100_000)
            person(unknown, "Noor")
            elder = person(older, "Elder", age=72)
            person(far, "Stranger", 500_000, firm=theirs)
            await s.commit()

            def ex(u=sophie):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def scan():
                run = await runtime.run_agent(s, firm=mine, agent_key=KEY, subject=Subject("firm", mine.id, mine.name), trigger="schedule")
                await s.commit()
                return run

            async def recs(status=None, firm=mine):
                stmt = select(Recommendation).where(Recommendation.firm_id == firm.id, Recommendation.agent_key == KEY.value)
                if status:
                    stmt = stmt.where(Recommendation.status == status)
                return (await s.execute(stmt.order_by(Recommendation.created_at))).scalars().all()

            async def waiting():
                return await recs(RecommendationStatus.PROPOSED)

            async def tasks():
                return (await s.execute(select(Task).where(Task.source == KEY.value))).scalars().all()

            async def decide(rec, action=HumanAction.APPROVE):
                done = await runtime.decide(s, firm=mine, recommendation=rec, action=action, actor_id=sophie.id, actor_label="Sophie Tran (adviser)")
                await s.commit()
                return done

            async def refused(label, call, says=None):
                try:
                    await call
                    check(label, "allowed", "refused")
                except ExecutorError as exc:
                    check(label, "refused", "refused")
                    if says:
                        check(f"  and says so: {says}", says in str(exc), True)

            print("\n=== a measured gap becomes one proposal ===")
            await scan()
            open_now = await waiting()
            check("one household has a gap", [r.subject_label for r in open_now], ["Chen Family"])
            rec = open_now[0]
            check("a household nobody can assess raises nothing", any(r.subject_label == "Unknown Family" for r in open_now), False)
            check("another firm is not read", await recs(firm=theirs), [])
            check("the title carries no figure", rec.title, "Protection gap - Chen Family")
            people = {g["person"] for g in rec.payload["gaps"]}
            check("it names each person with a gap", people, {"Wei", "Mei"})
            check("in the firm's currency", "$" in rec.summary and "if they die" in rec.summary, True)
            check("it says nothing is bought", "Nothing is bought, and no product is recommended." in rec.rationale, True)
            check("it says no cover on file is not no cover", "does not mean the client has none" in rec.rationale, True)
            check("and that no debts on file is not no debts", "does not mean there are none" in rec.rationale, True)
            check("it says a retirement age was assumed", "retirement age is assumed" in rec.rationale, True)
            check("and holds its confidence down for it", rec.confidence, 0.6)
            check("the gaps are largest first", [g["gap"] for g in rec.payload["gaps"]],
                  sorted((g["gap"] for g in rec.payload["gaps"]), reverse=True))
            check("the payload keeps the largest", rec.payload["largest_gap"], rec.payload["gaps"][0]["gap"])

            print("\n=== it stays one proposal, and closes when the gap does ===")
            await scan()
            check("a second scan refreshes it", [r.id for r in await waiting()], [rec.id])
            await s.refresh(rec)
            check("  and counts that it was found again", rec.payload["refreshed"]["times"], 2)
            cover = {"kind": "life", "provider": "Insurer", "source_document": "Policy schedule 2026", "recorded_by": "x"}
            wei_life = await pr.record_policy(s, mine.id, person_id=wei.id, sum_insured=40_000_000, **cover)
            mei_life = await pr.record_policy(s, mine.id, person_id=mei.id, sum_insured=40_000_000, **cover)
            tpd = {**cover, "kind": "tpd"}
            await pr.record_policy(s, mine.id, person_id=wei.id, sum_insured=40_000_000, **tpd)
            await pr.record_policy(s, mine.id, person_id=mei.id, sum_insured=40_000_000, **tpd)
            await s.commit()
            await scan()
            await s.refresh(rec)
            check("cover closing the gap closes the proposal", (str(rec.status), await waiting()), ("expired", []))
            for pol in await s.run_sync(lambda sync: sync.query(pr.ProtectionPolicy).all()):
                await pr.end_policy(s, mine.id, pol.id, reason="Test over", by="x")
            await s.commit()

            print("\n=== approving adds a review, and only that ===")
            await scan()
            rec = (await waiting())[0]
            card = await ex().preview_change("decide_recommendation", {"recommendation_id": str(rec.id), "action": "approve"})
            rows = {r["label"]: r["value"] for r in card["rows"]}
            check("the card names the client and the gaps", (rows["Client"], "if they die" in rows["Gaps"]), ("Chen Family", True))
            check("  and what approving does", rows["Decision"], "Approve, and add a task to review this cover")
            before = await s.run_sync(lambda sync: sync.query(pr.ProtectionPolicy).count())
            await decide(rec)
            await s.refresh(rec)
            check("it is carried out", str(rec.status), "executed")
            made = await tasks()
            check("one task", len(made), 1)
            t = made[0]
            check("to review with this client", (t.title, t.household_id, t.subject_label), ("Review protection with Chen Family", chen.id, "Chen Family"))
            check("held by the person who approved", (t.assigned_to, t.assigned_by), (sophie.id, sophie.id))
            check("with a date", t.due_date, today + timedelta(days=agent_mod.TASK_DUE_DAYS))
            check("  saying what was found", t.detail, rec.summary)
            check("no policy was touched", await s.run_sync(lambda sync: sync.query(pr.ProtectionPolicy).count()), before)
            await refused("the card refuses a decided proposal", ex().preview_change("decide_recommendation", {"recommendation_id": str(rec.id), "action": "approve"}), "Already")

            print("\n=== a decided gap stays quiet ===")
            await scan()
            check("the next scan leaves it alone", await waiting(), [])
            one = await ex().execute("read_protection", {"household_id": str(chen.id)})
            check("the read still shows it", one["summary"]["gaps"] > 0, True)
            await runtime.run_agent(s, firm=mine, agent_key=KEY, subject=Subject("household", chen.id, chen.name), trigger="manual")
            await s.commit()
            again = await waiting()
            check("a person asking about this household is told", [r.subject_label for r in again], ["Chen Family"])
            await decide(again[0], HumanAction.DISMISS)
            await scan()
            check("a dismissed gap stays quiet too", await waiting(), [])

            print("\n=== it comes back when it grows, or in six months ===")
            mei.profile = {"tax": {"annual_income": 300_000}}
            wei.profile = {"tax": {"annual_income": 600_000}}
            await s.commit()
            await scan()
            grown = await waiting()
            check("a larger gap comes back", [r.subject_label for r in grown], ["Chen Family"])
            await decide(grown[0], HumanAction.DISMISS)
            await s.refresh(grown[0])
            await scan()
            check("  once", await waiting(), [])
            grown[0].decided_at = utcnow() - timedelta(days=agent_mod.REVISIT_DAYS + 1)
            await s.commit()
            await scan()
            aged = await waiting()
            check("six months on it comes back the same size", [r.subject_label for r in aged], ["Chen Family"])

            print("\n=== approving again does not add a second task ===")
            await decide(aged[0])
            check("the open task is reused", len(await tasks()), 1)
            await s.refresh(t)
            await s.refresh(aged[0])
            check("  and says the latest", t.detail, aged[0].summary)
            check("  and says so", aged[0].payload["execution_result"]["added"], False)

            print("\n=== rolling back ===")
            await runtime.rollback(s, firm=mine, recommendation=aged[0], actor_id=sophie.id, actor_label="Sophie Tran (adviser)")
            await s.commit()
            await s.refresh(t)
            check("a decision that added no task removes none", str(t.status), "open")
            await scan()
            fresh = await waiting()
            check("a rolled back decision no longer quietens the gap", [r.subject_label for r in fresh], ["Chen Family"])
            t.status = "dismissed"
            await s.commit()
            await decide(fresh[0])
            second = [x for x in await tasks() if str(x.status) == "open"]
            check("with no task open a new one is added", len(second), 1)
            await runtime.rollback(s, firm=mine, recommendation=fresh[0], actor_id=sophie.id, actor_label="Sophie Tran (adviser)")
            await s.commit()
            await s.refresh(second[0])
            check("rolling that back takes it away", str(second[0].status), "dismissed")

            print("\n=== at Tier 3 nothing happens with nobody deciding ===")
            await scan()
            top = (await waiting())[0]
            ctx_tasks = len(await tasks())
            s.add(AgentConfig(firm_id=mine.id, agent_key=KEY, enabled=True, default_tier="tier_3"))
            await s.commit()
            agent = get_agent(KEY)
            result = await agent.act(type("C", (), {"session": s, "firm": mine, "subject": Subject("household", chen.id, chen.name)})(), top)
            check("act refuses", result["executed"], False)
            check("no task is added", len(await tasks()), ctx_tasks)

            print("\n=== care, on the record ===")
            view = await pr.for_household(s, older.id, firm_id=mine.id, today=today)
            someone = view["people"][0]
            check("with no cost set, an older person is not assessed", (someone["care"]["status"], someone["care"]["missing"]), ("not_assessed", ["care_cost"]))
            check("  and care is listed as left out", "long_term_care" in view["not_modelled"], True)
            check("  and the basis says none", view["care_basis"], None)
            young = await pr.for_household(s, chen.id, firm_id=mine.id, today=today)
            check("a 45-year-old is not yet the question", {p["care"]["status"] for p in young["people"]}, {"not_yet"})

            good = {"annual_cost": 90_000, "years": 3, "source_document": "Aged care cost survey, 2026, Ministry of Health"}
            await refused("an adviser cannot set it", ex().execute("set_care_assumptions", good), "branch leader")
            for label, change, says in (("no figure", {"annual_cost": None}, "yearly cost"), ("a zero", {"annual_cost": 0}, "yearly cost"),
                                        ("a negative", {"annual_cost": -1}, "cannot be negative"), ("no length", {"years": None}, "whole number"),
                                        ("no years", {"years": 0}, "between 1"), ("too long", {"years": 41}, "between 1"),
                                        ("no source", {"source_document": ""}, "which document")):
                await refused(f"refused on the card: {label}", ex(mark).preview_change("set_care_assumptions", {**good, **change}), says)
            card = await ex(mark).preview_change("set_care_assumptions", good)
            rows = {r["label"]: r["value"] for r in card["rows"]}
            check("the card names the figures", (rows["Cost of care"], rows["Planned for"], rows["Each person's need"]), ("$90,000 a year", "3 years", "$270,000"))
            check("  what it replaces", rows["Replaces"], "Nothing set")
            check("  and where it came from", rows["Document"], good["source_document"])
            check("the card writes nothing", pr.care_basis(await s.get(Firm, mine.id)), None)
            result = await ex(mark).execute("set_care_assumptions", good)
            await s.commit()
            firm_now = await s.get(Firm, mine.id)
            await s.refresh(firm_now)
            check("it is set", (firm_now.settings["protection"]["care"]["annual_cost"], firm_now.settings["protection"]["care"]["years"]), (90_000.0, 3))
            check("with who and from what", (firm_now.settings["protection"]["care"]["set_by"], firm_now.settings["protection"]["care"]["source"]), (mark.email, good["source_document"]))
            check("other firm settings stay", firm_now.settings["pii_redaction"], True)
            check("the replacement rates stay", pr.assumptions(firm_now)["death_replacement"], 0.7)
            entries = (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == "protection_care_basis"))).scalar_one()
            check("it goes on the ledger", entries, 1)
            view = await pr.for_household(s, older.id, firm_id=mine.id, today=today)
            someone = view["people"][0]
            check("care is now worked", (someone["care"]["status"], someone["care"]["need"]), ("gap", 270_000.0))
            check("care comes off the left-out list", "long_term_care" in view["not_modelled"], False)
            check("  while the state's part stays on it", "state_care_funding" in view["not_modelled"], True)
            check("the household counts as having a gap", view["summary"]["gaps"], 1)
            other = await pr.for_household(s, far.id, firm_id=theirs.id, today=today)
            check("another firm's care is untouched", other["care_basis"], None)

            print("\n=== care cover ===")
            base = {"person_id": str(elder.id), "kind": "long_term_care", "provider": "Insurer", "source_document": "Care policy 2026"}
            await refused("care cover needs its benefit", ex().preview_change("record_protection_policy", base), "annual benefit")
            await refused("a period that is not a number", ex().preview_change("record_protection_policy", {**base, "annual_benefit": 30_000, "benefit_years": "soon"}), "whole number")
            await refused("a period of nothing", ex().preview_change("record_protection_policy", {**base, "annual_benefit": 30_000, "benefit_years": 0}), "between 1")
            card = await ex().preview_change("record_protection_policy", {**base, "annual_benefit": 30_000, "benefit_years": 2})
            rows = {r["label"]: r["value"] for r in card["rows"]}
            check("the card names the cover and how long", (rows["Cover"], rows["Annual benefit"], rows["Paid for"]), ("Long-term care", "$30,000", "2 years"))
            card = await ex().preview_change("record_protection_policy", {**base, "annual_benefit": 30_000})
            check("  or says it lasts", {r["label"]: r["value"] for r in card["rows"]}["Paid for"], "As long as care lasts")
            await ex().execute("record_protection_policy", {**base, "annual_benefit": 30_000, "benefit_years": 2})
            await s.commit()
            view = await pr.for_household(s, older.id, firm_id=mine.id, today=today)
            care = view["people"][0]["care"]
            check("it counts for the years it pays", (care["cover"], care["gap"]), (60_000.0, 210_000.0))
            check("and shows on file", (view["policies"][0]["kind"], view["policies"][0]["benefit_years"]), ("long_term_care", 2))
            check("it does not count on death", view["people"][0]["death"].get("cover", 0), 0)

            print("\n=== the agent raises a care gap too ===")
            await scan()
            found = {r.subject_label: r for r in await waiting()}
            check("a retired household with a care gap is raised", "Older Family" in found, True)
            gap = found["Older Family"].payload["gaps"][0]
            check("  as care", (gap["person"], gap["scenario"], gap["gap"]), ("Elder", "care", 210_000.0))
            check("  in words", "if they need care" in found["Older Family"].summary, True)
    finally:
        await engine.dispose()


def main() -> int:
    gating()
    care_sums()
    asyncio.run(behaviour())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
