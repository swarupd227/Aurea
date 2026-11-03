"""Prove whose client a household is comes from the adviser edges, an edge to the household outranking one to a
member; that an adviser's day counts only their own households, from what is on file; that any other role sees the
firm's; that assigning needs a reason and replaces every earlier edge, on the audit log; that a supervision proposal
is compliance's and the branch leader's, not an adviser's; and that hours reclaimed count decisions, not runs.

Runs against a throwaway SQLite database.

    python -m tests.verify_advisers
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.atlas import history
from app.aurea_core import advisers, firm_time, reviews
from app.core import decision_rights
from app.core.db import Base, utcnow
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.cash_movement import CashRequest
from app.models.engagement import ClientReview, Meeting, Task
from app.models.enums import MeetingStatus, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import AuditEvent, User
from app.models.money_in import IncomingDeposit
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, advisers.AdviserError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rights() -> None:
    print("\n=== A supervision proposal is compliance's and the branch leader's ===")
    key = "conduct_surveillance"
    check("an adviser is not asked about it", decision_rights.queue_action(UserRole.ADVISER, key), None)
    check("compliance is asked to decide it", decision_rights.queue_action(UserRole.COMPLIANCE, key), "decide")
    check("so is a branch leader", decision_rights.queue_action(UserRole.BRANCH_LEADER, key), "decide")
    try:
        decision_rights.check(UserRole.ADVISER, key, "approve")
        check("an adviser approving it is refused", "allowed", "refused")
    except decision_rights.DecisionForbidden as e:
        check("an adviser approving it is refused, and says why", "supervising" in str(e), True)
    check("hours count decided proposals only", history.hours_reclaimed(12), 5.0)


async def main() -> int:
    rights()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add(firm)
            await s.flush()

            def user(role, name):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{name}@t", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u

            ann, ben = user(UserRole.ADVISER, "Ann Adviser"), user(UserRole.ADVISER, "Ben Adviser")
            cara = user(UserRole.ADVISER, "Cara Adviser")
            lead, ops = user(UserRole.BRANCH_LEADER, "Lee Leader"), user(UserRole.OPERATIONS, "Oli Ops")
            hs = {n: Household(id=uuid.uuid4(), firm_id=firm.id, name=n) for n in ("Ames", "Brook", "Cole", "Dunn")}
            s.add_all(hs.values())
            await s.flush()
            people = {n: Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=f"{n} One") for n, h in hs.items()}
            s.add_all(people.values())
            await s.flush()
            # The seed's way (to a member) and the admin page's way (to the household); Cole has both.
            s.add_all([
                RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="person", to_id=people["Ames"].id),
                RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="household", to_id=hs["Brook"].id),
                RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="person", to_id=people["Cole"].id),
                RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ben.id, to_type="household", to_id=hs["Cole"].id),
            ])
            mandates = {n: Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=n, mandate_type="advisory", suitability={}, constraints={})
                        for n, p in people.items()}
            s.add_all(mandates.values())
            await s.flush()
            accounts = {n: Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{n} cash", currency="NZD", cash_balance=50000)
                        for n, m in mandates.items()}
            s.add_all(accounts.values())
            await s.flush()

            print("\n=== Whose client each household is ===")
            held = await advisers.of_households(s, firm.id)
            check("a member's edge names the household; the household's own edge outranks a member's",
                  (held.get(hs["Ames"].id), held.get(hs["Brook"].id), held.get(hs["Cole"].id), hs["Dunn"].id in held), (ann.id, ann.id, ben.id, False))
            ex = lambda u: ToolExecutors(s, u.id, u.role, firm.id)  # noqa: E731
            book = await ex(ops).execute("read_advisers", {})
            check("Ann holds two, Ben one, Cara none, and one is unassigned",
                  ({a["name"]: a["households"] for a in book["advisers"]}, book["unassigned"]), ({"Ann Adviser": 2, "Ben Adviser": 1, "Cara Adviser": 0}, 1))

            print("\n=== An adviser's day ===")
            today, now = firm_time.today(firm), utcnow()
            run = AgentRun(firm_id=firm.id, agent_key="drift_rebalancing", status="awaiting", tier="tier_2", trigger="manual", started_at=now)
            s.add(run)
            await s.flush()

            def proposal(agent, subject_type, subject_id, title, status=RecommendationStatus.PROPOSED):
                s.add(Recommendation(firm_id=firm.id, run_id=run.id, agent_key=agent, tier="tier_2", status=status, title=title, summary="x",
                                     subject_type=subject_type, subject_id=subject_id, subject_label=title))
            proposal("drift_rebalancing", "mandate", mandates["Ames"].id, "Rebalance Ames")
            proposal("client_care", "household", hs["Brook"].id, "Call Brook")
            proposal("client_care", "household", hs["Cole"].id, "Call Cole")
            proposal("conduct_surveillance", "firm", firm.id, "Conduct surveillance review")
            proposal("client_care", "household", hs["Ames"].id, "Already decided", RecommendationStatus.APPROVED)
            s.add(ClientReview(firm_id=firm.id, household_id=hs["Ames"].id, held_on=today - timedelta(days=420), covered=[], recorded_by="a", recorded_by_role="adviser"))
            s.add(ClientReview(firm_id=firm.id, household_id=hs["Brook"].id, held_on=reviews.add_months(today, -12) + timedelta(days=3),
                               covered=[], recorded_by="a", recorded_by_role="adviser"))
            s.add(ClientReview(firm_id=firm.id, household_id=hs["Cole"].id, held_on=today - timedelta(days=420), covered=[], recorded_by="a", recorded_by_role="adviser"))
            s.add(Task(firm_id=firm.id, household_id=hs["Ames"].id, title="Send the Ames form", due_date=today + timedelta(days=2), assigned_to=ann.id))
            s.add(Task(firm_id=firm.id, household_id=hs["Ames"].id, title="Next month", due_date=today + timedelta(days=30), assigned_to=ann.id))
            s.add(CashRequest(firm_id=firm.id, account_id=accounts["Ames"].id, kind="withdrawal", amount=12000, payee="Ames One", reason="School fees", requested_by="a"))
            s.add(CashRequest(firm_id=firm.id, account_id=accounts["Cole"].id, kind="withdrawal", amount=9000, payee="Cole One", reason="Car", requested_by="b"))
            s.add(IncomingDeposit(firm_id=firm.id, account_id=accounts["Brook"].id, method="ach", amount=30000, received_on=today,
                                  hold_until=today + timedelta(days=5), evidence="Credit advice", recorded_by="o"))
            s.add(Meeting(id=uuid.uuid4(), firm_id=firm.id, household_id=hs["Ames"].id, title="Annual review", scheduled_at=now + timedelta(days=2),
                          status=MeetingStatus.SCHEDULED, brief={"agenda": ["x"]}))
            s.add(Meeting(id=uuid.uuid4(), firm_id=firm.id, household_id=hs["Brook"].id, title="Check-in", scheduled_at=now + timedelta(days=3),
                          status=MeetingStatus.SCHEDULED, brief={}))
            s.add(Meeting(id=uuid.uuid4(), firm_id=firm.id, household_id=hs["Ames"].id, title="Too far", scheduled_at=now + timedelta(days=20),
                          status=MeetingStatus.SCHEDULED))
            await s.flush()

            d = await ex(ann).execute("read_my_day", {})
            check("Ann's day covers her two households", (d["scope"], d["households"]), ("yours", 2))
            check("waiting: her mandate's rebalance and Brook's call, not Cole's (Ben's) nor the firm's surveillance review",
                  sorted(i["title"] for i in d["waiting"]["items"]), ["Call Brook", "Rebalance Ames"])
            check("due: Ames's overdue review, Brook's in three days, and her task this week", (d["due"]["count"], d["due"]["overdue"], d["due"]["reviews"], d["due"]["tasks"]),
                  (3, 1, 2, 1))
            check("money: Ames's payment and Brook's held deposit, not Cole's", (d["money"]["payments"], d["money"]["deposits_held"], d["money"]["value"]), (1, 1, 42000.0))
            check("meetings this week, one briefed", (d["meetings"]["count"], d["meetings"]["briefed"]), (2, 1))

            c = await ex(cara).execute("read_my_day", {})
            check("an adviser with no households has an empty day, and is told so", (c["households"], c["waiting"]["count"], "No households are assigned to you" in c["message"]),
                  (0, 0, True))
            f = await ex(lead).execute("read_my_day", {})
            check("a branch leader sees the firm: the surveillance review is theirs, the rebalance is not",
                  (f["scope"], f["households"], sorted(i["title"] for i in f["waiting"]["items"])), ("firm", 4, ["Call Brook", "Call Cole", "Conduct surveillance review"]))

            comp = user(UserRole.COMPLIANCE, "Cy Compliance")
            await s.flush()

            async def listed(u):
                out = await ex(u).execute("read_recommendations", {})
                return sorted(r["title"] for r in out[next(k for k, v in out.items() if isinstance(v, list))])
            check("an adviser's list leaves the surveillance review out", "Conduct surveillance review" in await listed(ann), False)
            check("compliance's list has it", "Conduct surveillance review" in await listed(comp), True)
            check("so does a branch leader's", "Conduct surveillance review" in await listed(lead), True)

            print("\n=== Assigning ===")
            ask = {"household_ids": f"{hs['Ames'].id},{hs['Dunn'].id}", "adviser": "Cara", "reason": "Ann is going part time"}
            await refused("a reason is needed", ex(ops).preview_change("assign_adviser", {**ask, "reason": " "}), "reason")
            await refused("an adviser nobody is called is refused, naming the firm's", ex(ops).preview_change("assign_adviser", {**ask, "adviser": "Zed"}), "Ann Adviser")
            await refused("a name that fits two is refused", ex(ops).preview_change("assign_adviser", {**ask, "adviser": "Adviser"}), "Name one")
            card = {r["label"]: r["value"] for r in (await ex(ops).preview_change("assign_adviser", ask))["rows"]}
            check("the card names the households, the adviser, who had them and why",
                  (card["Households"], card["Adviser"], card["Currently"], card["Reason"]), ("2: Ames, Dunn", "Cara Adviser", "Ann Adviser, Unassigned", "Ann is going part time"))
            await ex(ops).execute("assign_adviser", ask)
            held = await advisers.of_households(s, firm.id)
            check("both are Cara's now", (held[hs["Ames"].id], held[hs["Dunn"].id]), (cara.id, cara.id))
            left = (await s.execute(select(RelationshipEdge).where(RelationshipEdge.kind == "adviser", RelationshipEdge.to_id == people["Ames"].id))).scalars().all()
            check("the old edge to Ames's member is gone, so nothing reads Ann", len(left), 0)
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "client.adviser_assigned"))).scalars().all()
            check("each change is on the audit log with its reason", sorted((a.subject, a.detail["from"], a.detail["reason"]) for a in audit),
                  [("Ames", "Ann Adviser", "Ann is going part time"), ("Dunn", None, "Ann is going part time")])
            await refused("assigning what is already so is refused", ex(ops).preview_change("assign_adviser", ask), "already")
            await ex(ops).execute("assign_adviser", {"household_ids": str(hs["Dunn"].id), "adviser": "none", "reason": "Client asked for a new adviser"})
            check("none leaves a household unassigned", hs["Dunn"].id in await advisers.of_households(s, firm.id), False)
            d = await ex(ann).execute("read_my_day", {})
            check("Ann's day follows: Brook alone", (d["households"], [i["title"] for i in d["waiting"]["items"]]), (1, ["Call Brook"]))
            check("only operations, a branch leader or an admin hold the tool; an adviser does not",
                  sorted(str(r) for r in UserRole if "assign_adviser" in tools_for_role(r)), ["admin", "branch_leader", "operations"])
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
