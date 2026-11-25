"""What gives a plan a reason to be looked at again, how fast it was, and a tier a person sets (B-188).

    python -m tests.verify_plan_triggers

Runs against a throwaway SQLite database:
  - a life event (a business sale among them) and an equity grant are triggers; a grant now raises one review task for the
    recorded adviser, as a life event does, and a life event still does;
  - the plan is touched by the next event on its record at or after the trigger was recorded, never one before it; the days
    run from the day it was recorded to that event's day, on the firm's calendar; an open trigger shows how long it waits;
  - no target is supplied; the firm's list is longest-waiting first;
  - a tier is a full rebuild or a review and refresh, set by an adviser or paraplanner with a reason, added and never edited,
    the latest standing, never worked out; a client, operations and compliance cannot set it;
  - it changes no plan and no task beyond the one a grant raises.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import equity_comp, firm_time, plan_triggers
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_file import LifeEvent
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.equity_comp import EquityGrant
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.plan_record import PlanRecordEvent
from app.models.plan_tier import PlanTier
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, plan_triggers.PlanTriggerError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            s.add(firm)
            await s.flush()
            today = firm_time.today(firm)
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@t", full_name=r.value, role=r, hashed_password="x")
                     for r in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.CLIENT, UserRole.OPERATIONS, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER)}
            s.add_all(users.values())
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            quiet = Household(id=uuid.uuid4(), firm_id=firm.id, name="Quiet")
            s.add_all([h, quiet])
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee")
            s.add(ann)
            await s.flush()

            def ex(role=UserRole.ADVISER):
                u = users[role]
                return ToolExecutors(s, u.id, u.role, firm.id)

            print("\n=== Nothing on file ===")
            v = await plan_triggers.for_household(s, h.id, firm_id=firm.id)
            check("no triggers, and no tier is not set", (v["triggers"], v["tier"], v["summary"]["median_days"]), ([], None, None))
            check("no target is supplied", v["target"].startswith("none"), True)

            print("\n=== Triggers and how fast the plan answered ===")
            d = lambda n: today - timedelta(days=n)  # noqa: E731
            sale = LifeEvent(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, person_id=ann.id, kind="business_sale", happened_on=d(40),
                             source_document="Sale agreement", recorded_by="a@t", created_at=at(d(30)))
            marriage = LifeEvent(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, kind="marriage", happened_on=d(20), source_document="Certificate",
                                 recorded_by="a@t", created_at=at(d(10)))
            s.add_all([sale, marriage])
            s.add(PlanRecordEvent(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, seq=1, kind="baseline", plan_version=1, happened_on=d(35),
                                  recorded_by="a@t", recorded_by_role="adviser", snapshot={}, created_at=at(d(35))))
            await s.flush()
            v = await plan_triggers.for_household(s, h.id, firm_id=firm.id)
            by = {t["what"]: t for t in v["triggers"]}
            check("a business sale is a trigger, named", (by["business_sale"]["kind"], by["business_sale"]["person"]), ("business_sale", "Ann Lee"))
            check("a baseline recorded before a trigger does not answer it", (by["business_sale"]["answered_on"], by["business_sale"]["open_days"]), (None, 30))
            check("the clock runs from the day it was recorded, not the day it happened", by["business_sale"]["recorded_on"], d(30).isoformat())
            check("an open trigger shows how long it waits", by["marriage"]["open_days"], 10)
            s.add(PlanRecordEvent(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, seq=2, kind="review", plan_version=1, happened_on=d(25),
                                  recorded_by="p@t", recorded_by_role="paraplanner", snapshot={}, created_at=at(d(25))))
            await s.flush()
            v = await plan_triggers.for_household(s, h.id, firm_id=firm.id)
            by = {t["what"]: t for t in v["triggers"]}
            check("the next event on the record answers it, with the days between", (by["business_sale"]["answered_on"], by["business_sale"]["answered_by"],
                                                                                      by["business_sale"]["days_to_answer"]), (d(25).isoformat(), "review", 5))
            check("and not a trigger recorded after that event", (by["marriage"]["answered_on"], by["marriage"]["open_days"]), (None, 10))
            s.add(PlanRecordEvent(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, seq=3, kind="baseline", plan_version=2, happened_on=d(4),
                                  recorded_by="a@t", recorded_by_role="adviser", snapshot={}, created_at=at(d(4))))
            await s.flush()
            by = {t["what"]: t for t in (await plan_triggers.for_household(s, h.id, firm_id=firm.id))["triggers"]}
            check("a later baseline answers the marriage, six days on", (by["marriage"]["answered_by"], by["marriage"]["days_to_answer"]), ("baseline", 6))
            sm = (await plan_triggers.for_household(s, h.id, firm_id=firm.id))["summary"]
            check("the median is over the answered ones", (sm["answered"], sm["open"], sm["median_days"], sm["slowest_days"]), (2, 0, 5.5, 6))

            print("\n=== A recorded equity grant is a trigger, and raises one review task ===")
            tasks_before = (await s.execute(select(func.count(Task.id)))).scalar()
            grant = await equity_comp.record_grant(s, ann.id, firm_id=firm.id, by="a@t", kind="rsu", employer="Acme", units=1000, grant_date=d(2),
                                                   source_document="Acme plan statement, 2026")
            tasks = (await s.execute(select(Task).where(Task.source == "plan_trigger"))).scalars().all()
            check("one task opened, for the household", (len(tasks), tasks[0].household_id, tasks[0].title), (1, h.id, "Review after equity grant: Lee"))
            check("and no other task", (await s.execute(select(func.count(Task.id)))).scalar(), tasks_before + 1)
            check("it says what it touches", "equity compensation" in tasks[0].detail, True)
            v = await plan_triggers.for_household(s, h.id, firm_id=firm.id)
            g = next(t for t in v["triggers"] if t["kind"] == "equity_grant")
            check("the grant is a trigger", (g["what"], g["occurred_on"], g["answered_on"]), ("rsu", d(2).isoformat(), None))
            r = await ex().execute("record_equity_grant", {"person_id": str(ann.id), "kind": "rsu", "employer": "Acme", "units": 500, "grant_date": d(1).isoformat(),
                                                           "source_document": "Acme plan statement, second grant"})
            check("the conversation says a review task is open", "review task is open" in r["message"], True)

            print("\n=== The firm's list ===")
            f = await plan_triggers.for_firm(s, firm.id)
            check("only households with a trigger are listed", [r["household"] for r in f["households"]], ["Lee"])
            check("the two grants are open triggers", (f["summary"]["answered"], f["summary"]["open"]), (2, 2))
            check("tiers not set are counted", f["summary"]["tier_not_set"], 1)

            print("\n=== The tier ===")
            base = {"household_id": str(h.id), "tier": "full_rebuild", "reason": "Sold the family firm: the whole plan is new"}
            r = await ex().execute("set_plan_tier", base)
            check("set, with who set it", (r["tier"]["tier"], r["tier"]["set_by"]), ("full_rebuild", "adviser@t"))
            check("and says it changes no plan and no task", "changes no plan and no task" in r["message"], True)
            await refused("the same tier again", ex().execute("set_plan_tier", base), "already")
            await ex(UserRole.PARAPLANNER).execute("set_plan_tier", {**base, "tier": "review_and_refresh", "reason": "A grant only: refresh the figures"})
            v = await plan_triggers.for_household(s, h.id, firm_id=firm.id)
            check("a later tier stands, and both are kept", (v["tier"]["tier"], len(v["tier_history"])), ("review_and_refresh", 2))
            check("added and never edited", (await s.execute(select(func.count(PlanTier.id)))).scalar(), 2)
            await refused("a tier outside the two", ex().execute("set_plan_tier", {**base, "tier": "huge"}), "must be one of")
            await refused("no reason", ex().execute("set_plan_tier", {**base, "tier": "review_and_refresh", "reason": " "}), "why")
            await refused("a household in no firm", ex().execute("set_plan_tier", {**base, "household_id": str(uuid.uuid4())}), "not in this firm")
            for role in (UserRole.CLIENT, UserRole.OPERATIONS, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER):
                await refused(f"{role.value} cannot set it", ex(role).execute("set_plan_tier", {**base, "household_id": str(quiet.id)}), "adviser or a paraplanner")

            print("\n=== The tools ===")
            check("setting is a Confirm card; reading is a read", (TOOLS["set_plan_tier"].change_state, TOOLS["read_plan_triggers"].change_state),
                  (ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.NO))
            check("no client or operations tool reads it", {UserRole.CLIENT, UserRole.OPERATIONS} & TOOLS["read_plan_triggers"].roles_required, set())
            card = await ex().preview_change("set_plan_tier", {"household_id": str(quiet.id), "tier": "full_rebuild", "reason": "Two new businesses"})
            rows = {x["label"]: x["value"] for x in card["rows"]}
            check("the card says what it replaces and why", (rows["Plan tier"], rows["Replaces"], rows["Because"]), ("Full rebuild", "Not set", "Two new businesses"))
            read = await ex().execute("read_plan_triggers", {"household_id": str(h.id)})
            check("Astra reads the triggers and says no target is supplied", "No target is supplied" in read["message"], True)
            firmwide = await ex(UserRole.BRANCH_LEADER).execute("read_plan_triggers", {})
            check("a branch leader reads the firm", len(firmwide["households"]) >= 1, True)
            await refused("operations reads none of it", ex(UserRole.OPERATIONS).execute("read_plan_triggers", {}), "plan record is read by")
            check("a grant is a row, never edited", (await s.execute(select(func.count(EquityGrant.id)))).scalar(), 2)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
