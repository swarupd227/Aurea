"""Approving a proposal does what its card says (B-127).

    python -m tests.verify_approval_tasks

Eleven agents kept the base act(), so approving their proposals only stamped them. Against a throwaway SQLite
database, through the real runtime, this checks that:

  - no registered agent keeps the base act() any more;
  - approving one of the eleven adds one task, for the household's recorded adviser, or unassigned when none is;
  - the conduct review's task is compliance's, about the firm, closes no flag and resumes no agent;
  - a second approval while the first task is open brings it up to date instead of adding another;
  - rolling back takes an open task off the list and leaves a worked one alone;
  - dismissing adds nothing, and another firm's tasks are never touched;
  - the Confirm card's "On approval" row says what approving does, for every one of the eleven.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents._follow_up import FollowUpOnApproval
from app.atlas import runtime
from app.atlas.base import BaseAgent
from app.atlas.registry import all_agents, get_agent
from app.aurea_core import firm_time
from app.core.db import Base, utcnow
from app.llm.executors import ToolExecutors
from app.models.engagement import Task
from app.models.enums import AgentKey, AgentRunStatus, HumanAction, RecommendationStatus, SurveillanceSeverity, TaskStatus, UserRole
from app.models.governance import AgentRun, Recommendation, SurveillanceFlag
from app.models.graph import Household, RelationshipEdge
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole
ELEVEN = [
    AgentKey.NEXT_BEST_ACTION, AgentKey.CONDUCT_SURVEILLANCE, AgentKey.ESTATE_SUCCESSION, AgentKey.TAX_INTELLIGENCE,
    AgentKey.BEHAVIOURAL_FINANCE, AgentKey.REGULATORY_COUNTDOWN, AgentKey.IPS_DRAFTING, AgentKey.ASSET_LOCATION,
    AgentKey.WALLET_SHARE_SCOUT, AgentKey.GLIDE_PATH, AgentKey.REVERSE_CHURNING,
]


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def wiring() -> None:
    print("\n=== every agent's approval does something ===")
    idle = sorted(k.value for k, a in all_agents().items() if type(a).act is BaseAgent.act)
    check("no registered agent keeps the base act()", idle, [])
    for key in ELEVEN:
        agent = get_agent(key)
        check(f"{key.value} adds a follow-up task", isinstance(agent, FollowUpOnApproval), True)
    check("the conduct review is compliance's", get_agent(AgentKey.CONDUCT_SURVEILLANCE).follow_up_owner, "compliance")
    check("the rest go to the household's adviser",
          {get_agent(k).follow_up_owner for k in ELEVEN if k != AgentKey.CONDUCT_SURVEILLANCE}, {"adviser"})


async def behaviour() -> None:
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
            today = firm_time.today(mine)

            def user(firm, name, role, active=True):
                return User(id=uuid.uuid4(), firm_id=firm.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=role, is_active=active)
            sophie = user(mine, "Sophie Tran", R.ADVISER)
            gone = user(mine, "Left Firm", R.ADVISER, active=False)
            carla = user(mine, "Carla Ruiz", R.COMPLIANCE)
            s.add_all([sophie, gone, carla])
            chen = Household(id=uuid.uuid4(), firm_id=mine.id, name="Chen Family")
            nobody = Household(id=uuid.uuid4(), firm_id=mine.id, name="Nobody's Family")
            orphan = Household(id=uuid.uuid4(), firm_id=mine.id, name="Orphan Family")
            far = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Far Family")
            s.add_all([chen, nobody, orphan, far])
            await s.flush()
            s.add_all([
                RelationshipEdge(firm_id=mine.id, kind="adviser", from_type="user", from_id=sophie.id, to_type="household", to_id=chen.id),
                RelationshipEdge(firm_id=mine.id, kind="adviser", from_type="user", from_id=gone.id, to_type="household", to_id=orphan.id),
            ])
            flag = SurveillanceFlag(firm_id=mine.id, category="conduct", severity=SurveillanceSeverity.HIGH,
                                    finding="x", resolved=False, auto_paused_agent=True)
            s.add(flag)
            # Another firm's open task from the same agent, which nothing here may touch.
            theirs_task = Task(firm_id=theirs.id, household_id=far.id, title="Theirs", status=TaskStatus.OPEN,
                               source=AgentKey.ESTATE_SUCCESSION.value)
            s.add(theirs_task)
            await s.commit()

            async def proposal(key, household=None, title="Finding", summary="What was found.", firm=mine):
                run = AgentRun(firm_id=firm.id, agent_key=key.value, status=AgentRunStatus.AWAITING_APPROVAL,
                               trigger="test", started_at=utcnow())
                s.add(run)
                await s.flush()
                subject = ("household", household.id, household.name) if household else ("firm", firm.id, firm.name)
                rec = Recommendation(firm_id=firm.id, run_id=run.id, agent_key=key.value, tier="tier_1",
                                     status=RecommendationStatus.PROPOSED, title=title, summary=summary, rationale="",
                                     confidence=0.8, subject_type=subject[0], subject_id=subject[1], subject_label=subject[2],
                                     payload={}, evidence={}, citations=[])
                s.add(rec)
                await s.commit()
                return rec

            async def decide(rec, action=HumanAction.APPROVE, who=sophie):
                done = await runtime.decide(s, firm=mine, recommendation=rec, action=action, actor_id=who.id,
                                            actor_label=f"{who.full_name}")
                await s.commit()
                return done

            async def tasks_of(key, firm=mine):
                return (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == key.value)
                                        .order_by(Task.created_at))).scalars().all()

            print("\n=== approving adds one task for the household's adviser ===")
            for key in ELEVEN:
                if key == AgentKey.CONDUCT_SURVEILLANCE:
                    continue
                rec = await decide(await proposal(key, chen, title=f"{key.value} finding"))
                rows = await tasks_of(key)
                agent = get_agent(key)
                check(f"{key.value}: executed, with one task", (rec.status, len(rows)), (RecommendationStatus.EXECUTED, 1))
                t = rows[0]
                check(f"{key.value}: for Sophie, on the Chens, due in {agent.follow_up_days} days",
                      (t.assigned_to, t.household_id, t.due_date, str(t.status)),
                      (sophie.id, chen.id, today + __import__("datetime").timedelta(days=agent.follow_up_days), TaskStatus.OPEN.value))
                check(f"{key.value}: says what to do and what was found",
                      (t.title.startswith(agent.follow_up_verb + ": "), t.detail, t.subject_label),
                      (True, "What was found.", "Chen Family"))

            print("\n=== nobody recorded, or someone who has left ===")
            key = AgentKey.WALLET_SHARE_SCOUT
            rec = await decide(await proposal(key, nobody))
            t = [x for x in await tasks_of(key) if x.household_id == nobody.id]
            check("no adviser on record: unassigned, and the note says so",
                  (len(t), t[0].assigned_to, "no adviser is recorded" in rec.payload["execution_result"]["note"]), (1, None, True))
            await decide(await proposal(key, orphan))
            t = [x for x in await tasks_of(key) if x.household_id == orphan.id]
            check("an adviser who has left is not given it", (len(t), t[0].assigned_to), (1, None))

            print("\n=== again while the first is open: one task, brought up to date ===")
            key = AgentKey.ESTATE_SUCCESSION
            first = (await tasks_of(key))[0]
            rec = await decide(await proposal(key, chen, title="Estate review - Chen Family (high risk)", summary="Will predates the marriage."))
            rows = await tasks_of(key)
            check("still one task", len(rows), 1)
            check("it says what was found this time", (rows[0].id, rows[0].detail, rows[0].title),
                  (first.id, "Will predates the marriage.", "Discuss with the client: Estate review - Chen Family (high risk)"))
            check("and the result says it was already open", (rec.payload["execution_result"]["added"], "already open" in rec.payload["execution_result"]["note"]),
                  (False, True))
            await runtime.rollback(s, firm=mine, recommendation=rec, actor_id=sophie.id, actor_label="Sophie Tran")
            await s.commit()
            await s.refresh(rows[0])
            check("rolling that one back leaves the task it did not add", str(rows[0].status), TaskStatus.OPEN.value)
            check("another firm's task from the same agent is untouched", (await s.get(Task, theirs_task.id)).detail, None)

            print("\n=== rolling back ===")
            key = AgentKey.GLIDE_PATH
            rec = (await s.execute(select(Recommendation).where(Recommendation.agent_key == key.value))).scalars().first()
            await runtime.rollback(s, firm=mine, recommendation=rec, actor_id=sophie.id, actor_label="Sophie Tran")
            await s.commit()
            check("an open task comes off the list", [str(t.status) for t in await tasks_of(key)], [TaskStatus.DISMISSED.value])
            key = AgentKey.ASSET_LOCATION
            rec = (await s.execute(select(Recommendation).where(Recommendation.agent_key == key.value))).scalars().first()
            worked = (await tasks_of(key))[0]
            worked.status = TaskStatus.DONE
            await s.commit()
            await runtime.rollback(s, firm=mine, recommendation=rec, actor_id=sophie.id, actor_label="Sophie Tran")
            await s.commit()
            await s.refresh(worked)
            check("a task already worked stays as it is", str(worked.status) != TaskStatus.DISMISSED.value, True)

            print("\n=== dismissing adds nothing ===")
            key = AgentKey.BEHAVIOURAL_FINANCE
            before = len(await tasks_of(key))
            await decide(await proposal(key, nobody), action=HumanAction.DISMISS)
            check("no task", len(await tasks_of(key)), before)

            print("\n=== the conduct review is compliance's ===")
            key = AgentKey.CONDUCT_SURVEILLANCE
            rec = await decide(await proposal(key, None, title="Conduct surveillance review", summary="1 open conduct flag."), who=carla)
            rows = await tasks_of(key)
            check("one task about the firm, unassigned, for compliance",
                  (rec.status, len(rows), rows[0].household_id, rows[0].assigned_to, "compliance" in rec.payload["execution_result"]["note"]),
                  (RecommendationStatus.EXECUTED, 1, None, None, True))
            await s.refresh(flag)
            check("no flag closes", flag.resolved, False)

            print("\n=== the Confirm card says what approving does ===")
            for key in ELEVEN:
                rec = await proposal(key, chen)
                line = ToolExecutors._on_approval(rec)
                check(f"{key.value}: names the task and its owner",
                      (line.startswith("A task for "), str(get_agent(key).follow_up_days) in line, "no other action" in line),
                      (True, True, False))
            check("the conduct line names compliance and keeps flags open",
                  ToolExecutors._on_approval(await proposal(AgentKey.CONDUCT_SURVEILLANCE)),
                  "A task for compliance to work through the open flags, due in 7 days; no flag closes and no paused agent resumes")
            check("the IPS draft line says a draft is not an IPS",
                  "a draft is not an IPS" in ToolExecutors._on_approval(await proposal(AgentKey.IPS_DRAFTING, chen)), True)
    finally:
        await engine.dispose()


def main() -> int:
    wiring()
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
