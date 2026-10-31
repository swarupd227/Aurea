"""Tasks from a sentence: the read, and the three changes behind their Confirm card.

    python -m tests.verify_tasks

Against a throwaway SQLite database, with the model scripted. Checks that:

  - who holds each tool, and that the changes pause;
  - the read is this firm's tasks, soonest due first, with what is overdue and unassigned;
  - the Confirm card names the task and the change, nothing moves until it is confirmed, it moves
    once, and "Not now" moves nothing;
  - a change that would only fail is refused instead of offered;
  - a task, a household or a colleague from another firm reads as not found;
  - a name is never guessed between two people, and a task never goes to a platform account;
  - the page's routes hold to the same rules;
  - today, overdue and a due date are the firm's calendar, not the server's.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.api import engage
from app.aurea_core import firm_time
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm import gateway as gw_mod
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import Gateway
from app.models.engagement import Task
from app.models.enums import TaskStatus, UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm
from app.models.thread import PendingAction, PendingActionStatus, Thread, ThreadKind

PASS, FAIL = [], []
R = UserRole


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
    for key in ("complete_task", "assign_task", "create_task"):
        tool = TOOLS[key]
        check(f"{key} pauses for confirmation", tool.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check(f"{key} is an adviser's and paraplanner's", tool.roles_required, {R.ADVISER, R.PARAPLANNER})
        check(f"an administrator holds no {key}", key in tools_for_role(R.ADMIN), False)
    for key in ("read_tasks", "complete_task", "assign_task", "create_task"):
        check(f"a client holds no {key}", key in tools_for_role(R.CLIENT), False)


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

            def person(firm, name, role=R.ADVISER, active=True):
                return User(id=uuid.uuid4(), firm_id=firm.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=role, is_active=active)
            sophie = person(mine, "Sophie Tran")
            sam = person(mine, "Sam Patel", R.PARAPLANNER)
            sara = person(mine, "Sara Patel", R.OPERATIONS)
            gone = person(mine, "Gone Away", active=False)
            client = person(mine, "Wei Chen", R.CLIENT)
            stranger = person(theirs, "Their Adviser")
            admin = person(mine, "Platform Admin", R.ADMIN)
            superadmin = person(mine, "Platform Superadmin", R.SUPERADMIN)
            s.add_all([sophie, sam, sara, gone, client, stranger, admin, superadmin])
            chen = Household(id=uuid.uuid4(), firm_id=mine.id, name="Chen Family")
            other_hh = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Their Client")
            s.add_all([chen, other_hh])
            await s.flush()

            def task(firm, title, **kw):
                t = Task(id=uuid.uuid4(), firm_id=firm.id, title=title, status=kw.pop("status", TaskStatus.OPEN), **kw)
                s.add(t)
                return t
            late = task(mine, "Send the pack", household_id=chen.id, subject_label=chen.name,
                        due_date=today - timedelta(days=3), source="meeting_companion", assigned_to=sophie.id)
            soon = task(mine, "Call the trustee", due_date=today + timedelta(days=4), assigned_to=sam.id)
            undated = task(mine, "Review the cash buffer", source="client_request")
            done = task(mine, "Old follow-up", status=TaskStatus.DONE, due_date=today - timedelta(days=30))
            theirs_task = task(theirs, "Their task", assigned_to=stranger.id)
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

            print("\n=== the read ===")
            r = await ex().execute("read_tasks", {})
            check("this firm's open tasks, soonest due first, undated last", [t["title"] for t in r["tasks"]],
                  ["Send the pack", "Call the trustee", "Review the cash buffer"])
            check("what is overdue and unassigned", (r["overdue"], r["unassigned"]), (1, 1))
            check("  and flagged on the task", [t["overdue"] for t in r["tasks"]], [True, False, False])
            check("a holder is named", r["tasks"][0]["assigned_to_name"], "Sophie Tran")
            check("it says the date it read", r["as_of"], today.isoformat())
            check("the sentence counts them", r["message"], "3 open task(s). 1 overdue, 1 unassigned.")
            check("a done task is not overdue", any(
                t["overdue"] for t in (await ex().execute("read_tasks", {"status": "done"}))["tasks"]), False)
            check("all of them", (await ex().execute("read_tasks", {"status": "all"}))["total"], 4)
            check("mine", [t["title"] for t in (await ex(sam).execute("read_tasks", {"assigned_to_me": True}))["tasks"]],
                  ["Call the trustee"])
            check("a household's", [t["title"] for t in (await ex().execute("read_tasks", {"household_id": str(chen.id)}))["tasks"]],
                  ["Send the pack"])
            check("a limit is honest about the rest", (lambda x: (x["shown"], x["total"]))(await ex().execute("read_tasks", {"limit": 1})), (1, 3))
            await refused("an unknown status", ex().execute("read_tasks", {"status": "urgent"}))
            await refused("another firm's household", ex().execute("read_tasks", {"household_id": str(other_hh.id)}), "not found")

            print("\n=== completing, through the Confirm card ===")
            async def thread():
                t = Thread(firm_id=mine.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=sophie.id, title="t")
                s.add(t)
                await s.commit()
                return t

            async def ask(tool, inputs, user=sophie):
                t = await thread()
                gw_mod.llm_service.stream = script(tool_round(tool, inputs), say("Done."))
                events = [e async for e in Gateway(s, user.id, user.role, user.firm_id).send_message(t.id, "please")]
                return t, next((e for e in events if e["type"] == "pending_action"), None)

            async def pending_of(t):
                return (await s.execute(select(PendingAction).where(PendingAction.thread_id == t.id))).scalars().first()

            async def count(*where):
                return (await s.execute(select(func.count()).select_from(Task).where(*where))).scalar_one()

            def rows(ev):
                return {r["label"]: r["value"] for r in ev["preview"]["rows"]}

            t1, ev = await ask("complete_task", {"task_id": str(late.id)})
            check("it pauses", ev is not None, True)
            check("the card names the task", rows(ev), {"Task": "Send the pack", "Client": "Chen Family", "Due": firm_time.spoken(today - timedelta(days=3))})
            await s.refresh(late)
            check("nothing changed yet", str(late.status), "open")
            await Gateway(s, sophie.id, sophie.role, sophie.firm_id).confirm_pending_action((await pending_of(t1)).id, False)
            await s.refresh(late)
            check("Not now changes nothing", str(late.status), "open")

            t2, ev = await ask("complete_task", {"task_id": str(late.id)})
            gw_mod.llm_service.stream = script(say("Done."))
            await Gateway(s, sophie.id, sophie.role, sophie.firm_id).confirm_pending_action((await pending_of(t2)).id, True)
            await s.refresh(late)
            check("confirming completes it", str(late.status), "done")
            check("the pause is settled", (await pending_of(t2)).status != PendingActionStatus.AWAITING_CONFIRMATION, True)

            t3, ev = await ask("complete_task", {"task_id": str(late.id)})
            check("a finished task is refused, not offered", ev, None)
            await refused("completing it again says why", ex().preview_change("complete_task", {"task_id": str(late.id)}), "already done")
            await refused("executing it again too", ex().execute("complete_task", {"task_id": str(late.id)}), "already done")
            await refused("another firm's task", ex().preview_change("complete_task", {"task_id": str(theirs_task.id)}), "not found")

            print("\n=== assigning ===")
            p = await ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Sophie"})
            check("the card shows who holds it and who will", {r["label"]: r["value"] for r in p["rows"]}["Held by"], "Sam Patel")
            check("  and who it goes to", {r["label"]: r["value"] for r in p["rows"]}["Assign to"], "Sophie Tran")
            await s.refresh(soon)
            check("a preview changes nothing", soon.assigned_to, sam.id)
            r = await ex().execute("assign_task", {"task_id": str(soon.id), "assignee_name": "sophie tran"})
            check("a full name in any case", r["tasks"][0]["assigned_to_name"], "Sophie Tran")
            check("  and who gave it is kept", soon.assigned_by, sophie.id)
            await ex(sam).execute("assign_task", {"task_id": str(soon.id), "assignee_name": "me"})
            check("'me' is the person asking", soon.assigned_to, sam.id)
            r = await ex().execute("assign_task", {"task_id": str(soon.id), "assignee_name": "nobody"})
            check("'nobody' unassigns", (soon.assigned_to, soon.assigned_by, r["unassigned"]), (None, None, 1))
            await refused("a name that fits two people", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Patel"}), "Sam Patel, Sara Patel")
            await refused("a name that fits nobody", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Zoe"}), "matches nobody")
            await refused("a client", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Wei Chen"}), "matches nobody")
            await refused("someone who has left", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Gone Away"}), "matches nobody")
            await refused("a colleague at another firm", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Their Adviser"}), "matches nobody")
            await refused("no name at all", ex().preview_change("assign_task", {"task_id": str(soon.id)}))
            await refused("an account that only runs the platform", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Platform Admin"}), "matches nobody")
            await refused("  nor the superadmin", ex().preview_change("assign_task", {"task_id": str(soon.id), "assignee_name": "Platform Superadmin"}), "matches nobody")
            await refused("a finished task", ex().execute("assign_task", {"task_id": str(done.id), "assignee_name": "me"}), "already done")

            print("\n=== adding ===")
            before = await count()
            due = (today + timedelta(days=7)).isoformat()
            p = await ex().preview_change("create_task", {"title": "  Call Wei  ", "household_id": str(chen.id), "due_date": due})
            check("the card shows what will be added, with the weekday", {r["label"]: r["value"] for r in p["rows"]},
                  {"Task": "Call Wei", "Client": "Chen Family", "Due": firm_time.spoken(date.fromisoformat(due)), "Assign to": "Sophie Tran"})
            check("a preview adds nothing", await count(), before)
            await s.commit()
            check("  even once committed", await count(), before)
            t4, ev = await ask("create_task", {"title": "Call Wei", "household_id": str(chen.id), "due_date": due})
            gw_mod.llm_service.stream = script(say("Done."))
            await Gateway(s, sophie.id, sophie.role, sophie.firm_id).confirm_pending_action((await pending_of(t4)).id, True)
            check("confirming adds one", await count(), before + 1)
            made = (await s.execute(select(Task).where(Task.title == "Call Wei"))).scalars().one()
            check("it goes to the person asking", (made.assigned_to, made.assigned_by), (sophie.id, sophie.id))
            check("for that client", (made.household_id, made.subject_label), (chen.id, "Chen Family"))
            check("from the conversation", made.source, "conversation")
            check("due then", made.due_date, date.fromisoformat(due))
            r = await ex().execute("create_task", {"title": "Firm-wide tidy", "assignee_name": "nobody"})
            check("a firm task may have no client or holder", (r["tasks"][0]["subject_label"], r["tasks"][0]["assigned_to"]), (None, None))
            await refused("no title", ex().preview_change("create_task", {"title": "   "}), "needs a title")
            await refused("a day that is not a date", ex().preview_change("create_task", {"title": "x", "due_date": "Friday"}), "YYYY-MM-DD")
            await refused("a day already gone, saying what today is",
                          ex().preview_change("create_task", {"title": "x", "due_date": (today - timedelta(days=1)).isoformat()}),
                          f"today is {firm_time.spoken(today)}")
            r = await ex().preview_change("create_task", {"title": "x", "due_date": today.isoformat()})
            check("due today is allowed", {x["label"]: x["value"] for x in r["rows"]}["Due"], firm_time.spoken(today))
            await refused("another firm's household", ex().preview_change("create_task", {"title": "x", "household_id": str(other_hh.id)}), "not found")

            print("\n=== the page's routes hold to the same rules ===")
            async def route_refuses(label, call, status):
                try:
                    await call
                    check(label, "allowed", status)
                except HTTPException as exc:
                    check(label, exc.status_code, status)
            people = await engage.task_assignees(firm=mine, db=s)
            check("anyone staff can see who a task can go to, without the admin list",
                  sorted(p["name"] for p in people), ["Sam Patel", "Sara Patel", "Sophie Tran"])
            await route_refuses("the page cannot give a task to another firm's person",
                                engage.assign_task(undated.id, engage.TaskAssignIn(assigned_to=stranger.id), user=sophie, firm=mine, db=s), 409)
            await route_refuses("the page cannot complete a task twice",
                                engage.complete_task(late.id, user=sophie, firm=mine, db=s), 409)
            await route_refuses("the page cannot reach another firm's task",
                                engage.complete_task(theirs_task.id, user=sophie, firm=mine, db=s), 404)
            listed = await engage.list_tasks(status="open", assigned_to_me=False, firm=mine, db=s, user=sophie)
            check("the page's list carries what is overdue", all("overdue" in t for t in listed), True)
            check("and is the same list", [t["title"] for t in listed][:1], [(await ex().execute("read_tasks", {}))["tasks"][0]["title"]])

            print("\n=== a declined change stays declined ===")
            heard: dict = {}
            async def hearing(**kw):
                heard.update(kw)
                for ev in say("Fine."):
                    yield ev
            def said_to_model():
                return "\n".join(str(m["content"]) for m in heard["messages"])
            t7, _ = await ask("create_task", {"title": "Declined one"})
            await Gateway(s, sophie.id, sophie.role, sophie.firm_id).confirm_pending_action((await pending_of(t7)).id, False)
            gw_mod.llm_service.stream = hearing
            async for _ in Gateway(s, sophie.id, sophie.role, sophie.firm_id).send_message(t7.id, "Something else"):
                pass
            check("the model hears that it was declined", "[Not done: create_task" in said_to_model() and "chose Not now" in said_to_model(), True)
            check("  which change it was", "Declined one" in said_to_model(), True)
            check("  before the new request", said_to_model().index("Not done") < said_to_model().index("Something else"), True)
            check("turns still alternate", all(a["role"] != b["role"] for a, b in zip(heard["messages"], heard["messages"][1:])), True)
            t8, _ = await ask("create_task", {"title": "Confirmed one"})
            gw_mod.llm_service.stream = script(say("Done."))
            await Gateway(s, sophie.id, sophie.role, sophie.firm_id).confirm_pending_action((await pending_of(t8)).id, True)
            gw_mod.llm_service.stream = hearing
            async for _ in Gateway(s, sophie.id, sophie.role, sophie.firm_id).send_message(t8.id, "Thanks"):
                pass
            check("a confirmed change is not marked", "Not done" in said_to_model(), False)

            print("\n=== today is the firm's ===")
            # 8pm UTC on a Friday is already Saturday morning in Auckland.
            friday_night = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
            check("New Zealand is a day ahead of UTC", firm_time.today(Firm(jurisdiction="NZ"), friday_night), date(2026, 10, 31))
            check("New York is still that Friday", firm_time.today(Firm(jurisdiction="US"), friday_night), date(2026, 10, 30))
            check("a firm can name its own zone",
                  firm_time.today(Firm(jurisdiction="US", settings={"timezone": "Pacific/Auckland"}), friday_night), date(2026, 10, 31))
            check("an unknown jurisdiction falls back to UTC", firm_time.today(Firm(jurisdiction="ZZ"), friday_night), date(2026, 10, 30))
            check("a mistyped zone falls back too",
                  firm_time.today(Firm(jurisdiction="NZ", settings={"timezone": "Mars/Base"}), friday_night), date(2026, 10, 30))
            check("said with its weekday", firm_time.spoken(date(2026, 11, 6)), "Friday 6 November 2026")
            due_today = task(mine, "Due on the firm's today", due_date=today)
            due_yesterday = task(mine, "Due the firm's yesterday", due_date=today - timedelta(days=1))
            await s.commit()
            flags = {t["title"]: t["overdue"] for t in (await ex().execute("read_tasks", {}))["tasks"]}
            check("a task due today is not overdue", flags["Due on the firm's today"], False)
            check("one due yesterday is", flags["Due the firm's yesterday"], True)
            check("the read says the firm's date", (await ex().execute("read_tasks", {}))["as_of"], today.isoformat())
            seen: dict = {}
            async def capturing(**kw):
                seen.update(kw)
                for ev in say("Fine."):
                    yield ev
            gw_mod.llm_service.stream = capturing
            t9 = Thread(firm_id=mine.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=sophie.id, title="t9")
            s.add(t9)
            await s.commit()
            async for _ in Gateway(s, sophie.id, sophie.role, sophie.firm_id).send_message(t9.id, "When is Friday?"):
                pass
            check("the model is told today without a tool", f"Today, on the firm's calendar, is {firm_time.spoken(today)}." in seen.get("system", ""), True)
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
