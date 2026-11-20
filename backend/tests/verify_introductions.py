"""The way in to a prospect, from people the firm already knows (B-200).

    python -m tests.verify_introductions

Against a throwaway SQLite database. Checks that:

  - a link is recorded only with its evidence, between two real people of this firm, once, in the recorder's name,
    and its Confirm card names both ends, the evidence and that nobody is introduced;
  - a contact's notes are never read as a link;
  - the best path runs from the member of staff through the client who knows the prospect, scored by closeness,
    recency and the introducer's standing, and names who should ask; staff already in touch are listed apart;
  - a prospect nobody is recorded as knowing has no path and says what is not on file;
  - the prospect brief carries the ways in, and another firm's contact is not found;
  - the agent proposes one ask per prospect not yet met, approving gives the asker a task due in a week, a second
    approval brings it up to date, rollback takes it off, and nothing is logged on the prospect;
  - the tools belong to the client desk, not to a client.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.aurea_core import introductions
from app.core.db import Base, utcnow
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.engagement import Task
from app.models.enums import TaskStatus, UserRole
from app.models.graph import Household, Person, RelationshipEdge
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, introductions.IntroductionError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rows_of(preview) -> dict:
    return {r["label"]: r["value"] for r in preview["rows"]}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ", settings={})
            other = Firm(id=uuid.uuid4(), name="Other", slug="o", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, other])
            await s.flush()

            def user(f, role, name):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name.lower()}@{f.slug}", full_name=name, role=role,
                         hashed_password="x", is_active=True)
                s.add(u)
                return u
            sophie, tom = user(firm, UserRole.ADVISER, "Sophie Tran"), user(firm, UserRole.ADVISER, "Tom Reid")
            bea = user(firm, UserRole.BRANCH_LEADER, "Bea Lim")
            await s.flush()
            ex = lambda u, f=firm: ToolExecutors(s, u.id, u.role, f.id)  # noqa: E731
            now = utcnow()

            chen = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Chen Family")
            hartley = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Hartley Family")
            s.add_all([chen, hartley])
            await s.flush()
            wei = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=chen.id, full_name="Wei Chen")
            mei = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=chen.id, full_name="Mei Chen")
            gone = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hartley.id, full_name="Arthur Hartley",
                          deceased_on=date(2025, 3, 1))
            s.add_all([wei, mei, gone])
            s.add_all([RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=sophie.id, to_type="household", to_id=chen.id, attributes={}),
                       RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=tom.id, to_type="household", to_id=hartley.id, attributes={})])

            def contact(name, owner, kind="prospect", **kw):
                c = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name=name, owner_user_id=owner.id, is_active=True,
                               contact_type=kind, **kw)
                s.add(c)
                return c
            james = contact("James Whitaker", sophie, source="event")
            dana = contact("Dana Okafor", sophie, kind="centre_of_influence")
            rohan = contact("Rohan Mehta", sophie, source="centre_of_influence")
            olivia = contact("Olivia Harris", tom)
            hemi = contact("Hemi Walker", sophie, source="referral", notes="Referred by the Chen family.")
            priya = contact("Priya Nathan", sophie)
            await s.flush()
            for c, stage in ((james, "lead"), (rohan, "qualified"), (olivia, "lead"), (hemi, "lead"), (priya, "qualified")):
                s.add(CrmOpportunity(firm_id=firm.id, contact_id=c.id, title=f"{c.full_name} - new", stage=stage, opened_at=now - timedelta(days=20)))
            s.add(CrmActivity(firm_id=firm.id, contact_id=dana.id, activity_type="call", occurred_at=now - timedelta(days=20),
                              detail="Catch-up on referrals", logged_by_user_id=sophie.id))
            s.add(CrmActivity(firm_id=firm.id, contact_id=rohan.id, activity_type="email", occurred_at=now - timedelta(days=4),
                              detail="Sent authorities", logged_by_user_id=tom.id))
            s.add(CrmActivity(firm_id=firm.id, contact_id=priya.id, activity_type="meeting", occurred_at=now - timedelta(days=2),
                              detail="Discovery meeting", logged_by_user_id=sophie.id))
            await s.flush()

            print("\n=== Recording a link ===")
            link = {"from_type": "person", "from_id": str(wei.id), "to_type": "contact", "to_id": str(james.id), "kind": "knows",
                    "closeness": "close", "how": "on the chamber of commerce board together",
                    "evidence": "Wei said so at his review on 12 Oct"}
            await refused("evidence is required", ex(sophie).preview_change("record_relationship", {**link, "evidence": ""}), "where this comes from")
            await refused("closeness is only what someone said", ex(sophie).preview_change("record_relationship", {**link, "closeness": "very"}), "close, known or met")
            await refused("both ends the same", ex(sophie).preview_change("record_relationship", {**link, "to_type": "person", "to_id": str(wei.id)}), "same")
            await refused("two staff are no way in", ex(sophie).preview_change("record_relationship",
                          {**link, "from_type": "user", "from_id": str(sophie.id), "to_type": "user", "to_id": str(tom.id)}), "two members of staff")
            await refused("someone who has died is not linked", ex(sophie).preview_change("record_relationship",
                          {**link, "from_id": str(gone.id)}), "died")
            await refused("another firm's people are not found", ex(sophie, other).preview_change("record_relationship", link), "no person")
            card = rows_of(await ex(sophie).preview_change("record_relationship", link))
            check("the Confirm card names the link, the evidence, who records it and that nobody is introduced",
                  (card["Link"], card["Closeness"], card["Evidence"], card["Recorded by"], card["Introduces"]),
                  ("Wei Chen knows James Whitaker", "close", "Wei said so at his review on 12 Oct", "Sophie Tran", "Nobody; nothing is sent"))
            out = await ex(sophie).execute("record_relationship", link)
            check("it is recorded in Sophie's name", (out["label"], out["recorded_by"]), ("knows", "Sophie Tran"))
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "relationship.recorded"))).scalars().all()
            check("  and on the audit log", len(audit), 1)
            await refused("the same link twice is refused", ex(tom).preview_change("record_relationship",
                          {**link, "from_type": "contact", "from_id": str(james.id), "to_type": "person", "to_id": str(wei.id)}), "already on file")
            await ex(sophie).execute("record_relationship", {"from_type": "contact", "from_name": "Dana Okafor", "to_type": "contact",
                                                             "to_name": "Rohan Mehta", "kind": "referred",
                                                             "evidence": "Dana's referral email, 24 Oct"})

            print("\n=== The ways in ===")
            found = await ex(sophie).execute("find_introductions", {"contact_id": str(james.id)})
            best = found["paths"][0]
            check("the best way in to James is through Wei, asked by Sophie, his adviser",
                  (best["introducer"]["name"], best["ask"]["name"], [h["to"]["name"] for h in best["hops"]]),
                  ("Wei Chen", "Sophie Tran", ["Wei Chen", "James Whitaker"]))
            check("scored on closeness, recency and standing: 0.8 x 0.9 x 1.0 x 1.1",
                  (best["score"], best["introducer"]["standing"]), (79, ["client", "adviser Sophie Tran"]))
            check("one path per introducer and asker (Mei's longer one through Wei is not listed again)",
                  len([p for p in found["paths"] if p["introducer"]["name"] == "Wei Chen"]), 1)
            check("the recorded hop carries its evidence and who recorded it",
                  (best["hops"][1]["recorded"], best["hops"][1]["evidence"], best["hops"][1]["recorded_by"]),
                  (True, "Wei said so at his review on 12 Oct", "Sophie Tran"))
            check("the message names the best path", "Sophie Tran to ask Wei Chen" in found["message"], True)

            found = await ex(sophie).execute("find_introductions", {"name": "Rohan Mehta"})
            best = found["paths"][0]
            check("Rohan through Dana, the centre of influence Sophie called, who referred him",
                  (best["introducer"]["name"], best["ask"]["name"], best["introducer"]["standing"], best["score"]),
                  ("Dana Okafor", "Sophie Tran", ["centre of influence", "referred 1 before"], 33))
            check("Tom, who emailed him, is listed as already in touch, not as a way in",
                  [t["user"]["name"] for t in found["in_touch"]], ["Tom Reid"])

            found = await ex(sophie).execute("find_introductions", {"contact_id": str(hemi.id)})
            check("a contact's notes ('Referred by the Chen family') are not read as a link",
                  (found["paths"], found["not_on_file"]), ([], ["Anyone recorded as knowing them"]))
            found = await ex(tom).execute("find_introductions", {"contact_id": str(olivia.id)})
            check("Olivia has no way in, and it says so", ("No way in" in found["message"], found["recorded_links_to_target"]), (True, 0))
            await refused("another firm cannot look up this firm's contact",
                          ex(sophie, other).execute("find_introductions", {"contact_id": str(james.id)}), "not one of this firm")
            await refused("a name that matches nobody", ex(sophie).execute("find_introductions", {"name": "Nobody Atall"}), "no contact or client")

            print("\n=== The prospect brief ===")
            brief = await ex(sophie).execute("prepare_prospect_meeting", {"contact_id": str(james.id)})
            check("it carries the ways in", brief["introductions"]["paths"][0]["introducer"]["name"], "Wei Chen")
            check("and is laid out the default way for Sophie, who has no profile",
                  (brief["shaped_for"]["set"], brief["shaped_for"]["order"][:2]), (False, ["deals", "introductions"]))
            check("the message names the best way in", "best way in: Wei Chen, asked by Sophie Tran" in brief["message"], True)

            print("\n=== The agent ===")
            from app.agents.warm_introduction import WarmIntroductionAgent
            from app.atlas.base import AgentContext, Subject
            agent = WarmIntroductionAgent()
            ctx = AgentContext(session=s, firm=firm, run=None, tier=agent.default_tier, guardrails={}, config={},
                               subject=Subject("firm", firm.id, firm.name))
            sensed = await agent.sense(ctx)
            drafts = await agent.think(ctx, sensed)
            check("one proposal per prospect with a way in, none for one already met or with none",
                  sorted(d.subject.label for d in drafts), ["James Whitaker", "Rohan Mehta"])
            d = next(d for d in drafts if d.subject.label == "James Whitaker")
            check("its title names the prospect and the introducer, with no number in it",
                  d.title, "Warm introduction — James Whitaker through Wei Chen")
            touches = (await s.execute(select(func.count()).select_from(CrmActivity))).scalar_one()

            class Rec:
                subject_id, subject_label, payload, modified_payload, decided_by, summary = (
                    james.id, "James Whitaker", d.payload, None, bea.id, d.summary)
            check("the Confirm card says what approving does", agent.on_approval(Rec()),
                  "A task for Sophie Tran to ask Wei Chen for an introduction, due in 7 days; nobody is contacted and nothing is logged on the prospect")
            res = await agent.act(ctx, Rec())
            task = await s.get(Task, uuid.UUID(res["task_id"]))
            check("approving gives Sophie a task to ask Wei, due in a week",
                  (task.assigned_to == sophie.id, task.title, (task.due_date - date.today()).days in (6, 7, 8)),
                  (True, "Ask Wei Chen to introduce James Whitaker", True))
            again = await agent.act(ctx, Rec())
            check("a second approval brings the same task up to date", (again["task_id"], again["added"]), (res["task_id"], False))
            check("nothing is logged on the prospect", (await s.execute(select(func.count()).select_from(CrmActivity))).scalar_one(), touches)
            Rec.payload = {**d.payload, "execution_result": res}
            back = await agent.rollback(ctx, Rec())
            await s.refresh(task)
            check("rolling back takes the open task off", (back["reversed"], task.status), (True, TaskStatus.DISMISSED))

            print("\n=== The pipeline page ===")
            from app.api import introductions as route
            page = await route.prospects(user=sophie, firm=firm, session=s)
            check("an adviser reads the prospects they own and have not met, each with its best way in or none",
                  (page["scope"], [(p["name"], (p["best"] or {}).get("introducer", {}).get("name")) for p in page["prospects"]]),
                  ("mine", [("Hemi Walker", None), ("James Whitaker", "Wei Chen"), ("Rohan Mehta", "Dana Okafor")]))
            firmwide = await route.prospects(user=bea, firm=firm, session=s)
            check("a branch leader reads the firm's, Tom's Olivia included", ("Olivia Harris" in [p["name"] for p in firmwide["prospects"]],
                                                                            firmwide["scope"]), (True, "firm"))
            try:
                await route.record(route.LinkIn(from_type="contact", from_id=str(olivia.id), to_type="person", to_id=str(mei.id),
                                                evidence=""), user=tom, firm=firm, session=s)
                check("the page's form is refused without evidence too", "allowed", "refused")
            except Exception as exc:
                check("the page's form is refused without evidence too", getattr(exc, "status_code", None), 422)

            print("\n=== Who may use it ===")
            check("an adviser and a branch leader may", all(k in tools_for_role(r) for r in (UserRole.ADVISER, UserRole.BRANCH_LEADER)
                                                          for k in ("find_introductions", "record_relationship")), True)
            check("a client may not", any(k in tools_for_role(UserRole.CLIENT) for k in ("find_introductions", "record_relationship")), False)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
