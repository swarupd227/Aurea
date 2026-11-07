"""Running a skill again brings its open proposals up to date instead of raising them again (B-135).

    python -m tests.verify_skill_reruns

Against a throwaway SQLite database, through the real skill runtime, with the model scripted. Checks that:

  - a first run raises one proposal per household the skill matches;
  - a second run raises none beside them: each is the same proposal, brought up to date, on the ledger;
  - a household matched for the first time gets a new proposal;
  - a proposal someone has decided is never touched, and the next run raises a new one;
  - a household the rerun no longer matches keeps its open proposal for a person to dismiss;
  - extras left in the queue by an earlier rerun are closed, saying which one stands;
  - another skill's proposals, and another firm's, are separate;
  - a test run saves nothing;
  - the tool's message says how many are new and how many were brought up to date.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.atlas import runtime as atlas_runtime
from app.core.db import Base, utcnow
from app.llm.executors import ToolExecutors
from app.models.enums import AgentRunStatus, HumanAction, RecommendationStatus, UserRole
from app.models.governance import AgentRun, LedgerEntry, Recommendation
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.skill import Skill
from app.models.tenant import Firm
from app.skills import runtime as skills

PASS, FAIL = [], []
KEY = skills.SKILL_AGENT_KEY

# What the scripted model says, by household name. Absent means it does not apply.
SAYS: dict[str, dict] = {}


async def scripted(session, firm, skill, brain) -> dict:
    said = SAYS.get(brain["household"]["name"])
    return {"applies": True, **said} if said else {"applies": False}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def behaviour() -> None:
    skills._ask = scripted
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            mine = Firm(id=uuid.uuid4(), name="Mine", slug="mine", base_currency="NZD")
            theirs = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs", base_currency="NZD")
            s.add_all([mine, theirs])
            await s.flush()
            sophie = User(id=uuid.uuid4(), firm_id=mine.id, email="sophie@x.test", full_name="Sophie Tran",
                          hashed_password="x", role=UserRole.ADVISER, is_active=True)
            s.add(sophie)
            hs = {}
            for firm, name in [(mine, "Chen"), (mine, "Patel"), (mine, "Ngata"), (mine, "Quiet"), (theirs, "Far")]:
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(h)
                await s.flush()
                s.add(Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=f"{name} One"))
                hs[name] = h
            cash = Skill(id=uuid.uuid4(), firm_id=mine.id, created_by=sophie.id, name="Cash drag finder",
                         instruction="Find clients with too much cash.", scope="book", output_kind="note")
            other = Skill(id=uuid.uuid4(), firm_id=mine.id, created_by=sophie.id, name="Birthday nudge",
                          instruction="Find birthdays.", scope="book", output_kind="note")
            far = Skill(id=uuid.uuid4(), firm_id=theirs.id, name="Cash drag finder", instruction="x", scope="book", output_kind="note")
            s.add_all([cash, other, far])
            await s.commit()

            async def run(skill=cash, firm=mine, **kw):
                out = await skills.run_skill(s, firm, skill, **kw)
                await s.commit()
                return out

            async def open_of(skill=cash, firm=mine):
                rows = (await s.execute(select(Recommendation).where(
                    Recommendation.firm_id == firm.id, Recommendation.agent_key == KEY,
                    Recommendation.status == RecommendationStatus.PROPOSED).order_by(Recommendation.created_at))).scalars().all()
                return [r for r in rows if r.payload.get("skill_id") == str(skill.id)]

            def by_house(recs):
                return {r.subject_label: r for r in recs}

            print("\n=== the first run ===")
            SAYS.clear()
            SAYS.update({"Chen": {"title": "Chen holds 18% in cash", "summary": "18% cash."},
                         "Patel": {"title": "Patel holds 15% in cash", "summary": "15% cash."}})
            first = await run()
            opened = await open_of()
            check("one proposal per matching household", sorted(r.subject_label for r in opened), ["Chen", "Patel"])
            check("nothing brought up to date yet", first["refreshed_ids"], [])
            first_ids = {r.subject_label: r.id for r in opened}

            print("\n=== the same skill again ===")
            SAYS["Chen"] = {"title": "Cash drag at the Chens: 21% idle", "summary": "21% cash now."}
            again = await run()
            opened = await open_of()
            check("still two open, not four", len(opened), 2)
            check("the same proposals, by id", {r.subject_label: r.id for r in opened}, first_ids)
            check("with this run's words, whatever title the model chose",
                  (by_house(opened)["Chen"].title, by_house(opened)["Chen"].summary), ("Cash drag at the Chens: 21% idle", "21% cash now."))
            check("the result names both as brought up to date", sorted(again["refreshed_ids"]), sorted(str(i) for i in first_ids.values()))
            check("and lists them as this run's proposals", sorted(again["recommendation_ids"]), sorted(str(i) for i in first_ids.values()))
            check("the skill is still named on each", {r.payload.get("skill_id") for r in opened}, {str(cash.id)})
            check("each says how often it has been seen", by_house(opened)["Chen"].payload["refreshed"]["times"], 2)
            refreshed = (await s.execute(select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.event_type == "recommendation_refreshed"))).scalar_one()
            check("each refresh is on the ledger", refreshed, 2)
            latest = (await s.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(again["run_id"])))).scalar_one()
            check("a run that raised nothing new is not waiting on anyone", str(latest.status), AgentRunStatus.COMPLETED.value)

            print("\n=== a household matched for the first time ===")
            SAYS["Ngata"] = {"title": "Ngata holds 30% in cash", "summary": "30% cash."}
            third = await run()
            opened = await open_of()
            check("it gets a new proposal, the others stay", (len(opened), len(third["refreshed_ids"])), (3, 2))
            latest = (await s.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(third["run_id"])))).scalar_one()
            check("and the run waits on that one", str(latest.status), AgentRunStatus.AWAITING_APPROVAL.value)

            print("\n=== a decided proposal is never touched ===")
            patel = by_house(opened)["Patel"]
            await atlas_runtime.decide(s, firm=mine, recommendation=patel, action=HumanAction.DISMISS,
                                       actor_id=sophie.id, actor_label="Sophie Tran")
            await s.commit()
            before = (patel.title, patel.summary)
            SAYS["Patel"] = {"title": "Patel holds 16% in cash", "summary": "16% cash."}
            await run()
            await s.refresh(patel)
            check("the dismissed one is as it was", (str(patel.status), patel.title, patel.summary),
                  (RecommendationStatus.DISMISSED.value, *before))
            check("and Patel has a new open one", [r.title for r in await open_of() if r.subject_label == "Patel"], ["Patel holds 16% in cash"])

            print("\n=== no longer matched ===")
            del SAYS["Ngata"]
            await run()
            check("Ngata's open proposal stays for a person to dismiss",
                  [r.subject_label for r in await open_of()].count("Ngata"), 1)

            print("\n=== extras an earlier rerun left ===")
            chen_now = by_house(await open_of())["Chen"]
            stray_run = AgentRun(firm_id=mine.id, agent_key=KEY, status=AgentRunStatus.AWAITING_APPROVAL, trigger="skill",
                                 started_at=utcnow())
            s.add(stray_run)
            await s.flush()
            stray = Recommendation(firm_id=mine.id, run_id=stray_run.id, agent_key=KEY, tier="tier_1",
                                   status=RecommendationStatus.PROPOSED, title="Older duplicate", summary="x",
                                   subject_type="household", subject_id=hs["Chen"].id, subject_label="Chen",
                                   payload={"skill_id": str(cash.id)}, evidence={}, citations=[])
            s.add(stray)
            await s.commit()
            check("two open for the Chens before the run", [r.subject_label for r in await open_of()].count("Chen"), 2)
            await run()
            chens = [r for r in await open_of() if r.subject_label == "Chen"]
            check("one after it", len(chens), 1)
            await s.refresh(stray)
            await s.refresh(chen_now)
            closed = stray if str(stray.status) == RecommendationStatus.EXPIRED.value else chen_now
            check("the other is closed, naming the one that stands",
                  (str(closed.status), str(chens[0].id) in (closed.decision_note or "")), (RecommendationStatus.EXPIRED.value, True))

            print("\n=== other skills and other firms are separate ===")
            SAYS.clear()
            SAYS["Chen"] = {"title": "Birthday next week", "summary": "Turns 60."}
            await run(other)
            check("another skill raises its own proposal for the Chens", len(await open_of(other)), 1)
            check("and the cash drag one is still there", [r.subject_label for r in await open_of()].count("Chen"), 1)
            SAYS["Far"] = {"title": "Far holds 40% in cash", "summary": "40%."}
            await run(far, firm=theirs)
            check("another firm's run raises its own", len(await open_of(far, firm=theirs)), 1)
            check("and leaves this firm's alone", sorted(r.subject_label for r in await open_of()), ["Chen", "Ngata", "Patel"])

            print("\n=== a test run saves nothing ===")
            count = (await s.execute(select(func.count()).select_from(Recommendation))).scalar_one()
            test = await run(persist=False)
            check("no proposal written, none refreshed", ((await s.execute(select(func.count()).select_from(Recommendation))).scalar_one(),
                                                       test["refreshed_ids"]), (count, []))

            print("\n=== the tool says what happened ===")
            SAYS.update({"Chen": {"title": "Chen cash", "summary": "x"}, "Quiet": {"title": "Quiet cash", "summary": "y"}})
            out = await ToolExecutors(s, sophie.id, sophie.role, mine.id).run_skill({"skill_id": str(cash.id)})
            await s.commit()
            check("one new and one brought up to date", (out["new"], out["brought_up_to_date"]), (1, 1))
            check("the message says so", "1 new proposal(s), and 1 still waiting from an earlier run brought up to date" in out["message"], True)
            check("each proposal says whether it was waiting",
                  sorted((p["household"], p["still_open"]) for p in out["proposals"]), [("Chen", True), ("Quiet", False)])
    finally:
        await engine.dispose()


def main() -> int:
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
