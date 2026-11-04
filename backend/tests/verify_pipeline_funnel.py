"""Prove the pipeline and onboarding read as one funnel, from lead to invested (B-159).

A deal is won only by opening its onboarding case, which takes the contact's name and email and is refused twice; the
case carries the deal to a household at activation and a reversal takes it back. A stage move records its reason, and
lost needs one. A probability nobody gave is none. An adviser reads their own contacts' deals, a branch leader the
firm's. A deal with nothing logged past its stage's target is quiet, and the follow-up agent raises it, gives the owner
a task on approval and moves nothing. A prospect's brief is what was recorded, with what is not on file.

Runs against a throwaway SQLite database.

    python -m tests.verify_pipeline_funnel
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.aurea_core import onboarding_start, pipeline
from app.core.db import Base, utcnow
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import UserRole
from app.models.graph import Household
from app.models.identity import AuditEvent, User
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, onboarding_start.StartError, pipeline.PipelineError, HTTPException) as e:
        text = str(getattr(e, "detail", e))
        ok = all(c.lower() in text.lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {text[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rows_of(preview) -> dict:
    rows = preview.get("rows") if isinstance(preview, dict) else preview
    return {r["label"] if isinstance(r, dict) else r[0]: (r["value"] if isinstance(r, dict) else r[1]) for r in rows}


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
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name.lower()}@{f.slug}", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u
            sophie, tom = user(firm, UserRole.ADVISER, "Sophie"), user(firm, UserRole.ADVISER, "Tom")
            lead, ops = user(firm, UserRole.BRANCH_LEADER, "Bea"), user(firm, UserRole.OPERATIONS, "Oli")
            await s.flush()
            ex = lambda u: ToolExecutors(s, u.id, u.role, firm.id)  # noqa: E731
            now = utcnow()

            def contact(name, owner, **kw):
                c = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name=name, owner_user_id=owner.id, is_active=True,
                               contact_type="prospect", **kw)
                s.add(c)
                return c
            priya = contact("Priya Nathan", sophie, email="priya@example.com", source="referral", notes="Sold a business.")
            marcus = contact("Marcus Webb", sophie)
            tara = contact("Tara Quinn", tom, email="tara@example.com")
            await s.flush()

            def deal(c, title, stage, days_ago, **kw):
                o = CrmOpportunity(id=uuid.uuid4(), firm_id=firm.id, contact_id=c.id, title=title, stage=stage,
                                   opened_at=now - timedelta(days=days_ago), **kw)
                s.add(o)
                return o
            p_deal = deal(priya, "Priya - new relationship", "proposal", 40, estimated_aum=750_000, probability_pct=60)
            m_deal = deal(marcus, "Marcus - new relationship", "lead", 30)
            t_deal = deal(tara, "Tara - rollover", "qualified", 5, estimated_aum=200_000)
            await s.flush()
            s.add(CrmActivity(firm_id=firm.id, contact_id=priya.id, opportunity_id=p_deal.id, activity_type="meeting",
                              occurred_at=now - timedelta(days=3), detail="Discovery meeting"))
            s.add(CrmActivity(firm_id=firm.id, contact_id=marcus.id, activity_type="call",
                              occurred_at=now - timedelta(days=25), detail="First call"))
            await s.flush()

            print("\n=== Whose funnel ===")
            mine = await pipeline.overview(s, firm, sophie)
            check("an adviser reads their own contacts' deals", (mine["scope"], sorted(r["contact"] for r in mine["opportunities"])),
                  ("mine", ["Marcus Webb", "Priya Nathan"]))
            firmwide = await pipeline.overview(s, firm, lead)
            check("a branch leader reads the firm's", (firmwide["scope"], len(firmwide["opportunities"])), ("firm", 3))
            check("the branch leader may read it in the conversation", "read_crm_pipeline" in tools_for_role(UserRole.BRANCH_LEADER), True)
            check("open by stage", [(x["stage"], x["count"]) for x in firmwide["stages"]], [("lead", 1), ("qualified", 1), ("proposal", 1)])

            print("\n=== Quiet deals ===")
            check("a lead with its last call 25 days ago is quiet against 21", [r["contact"] for r in firmwide["quiet"]], ["Marcus Webb"])
            check("a proposal met three days ago is not", next(r for r in firmwide["opportunities"] if r["id"] == str(p_deal.id))["quiet"], None)
            check("a weighted value counts only a probability someone gave",
                  (firmwide["open"]["weighted_aum"], firmwide["open"]["weighted_from"], firmwide["open"]["no_probability"]), (450_000.0, 1, 2))

            print("\n=== Moving a stage ===")
            await refused("won is not a move", ex(sophie).preview_change("update_crm_opportunity_stage",
                          {"opportunity_id": str(m_deal.id), "stage": "won"}), "start_onboarding")
            await refused("lost needs its reason", ex(sophie).preview_change("update_crm_opportunity_stage",
                          {"opportunity_id": str(m_deal.id), "stage": "lost"}), "why it was lost")
            card = rows_of(await ex(sophie).preview_change("update_crm_opportunity_stage",
                           {"opportunity_id": str(m_deal.id), "stage": "qualified", "reason": "Has 400k to move"}))
            check("the card names the move and the reason", (card.get("Stage"), card.get("Reason")), ("Lead becomes Qualified", "Has 400k to move"))
            await ex(sophie).execute("update_crm_opportunity_stage", {"opportunity_id": str(m_deal.id), "stage": "qualified", "reason": "Has 400k to move"})
            note = (await s.execute(select(CrmActivity).where(CrmActivity.opportunity_id == m_deal.id))).scalars().all()
            check("the move is on the deal's activity, with the reason, in the mover's name",
                  [(a.detail, a.logged_by_user_id == sophie.id) for a in note], [("Lead to Qualified: Has 400k to move", True)])
            after = await pipeline.overview(s, firm, sophie)
            check("logging the move touches the deal, so it is no longer quiet", [r["contact"] for r in after["quiet"]], [])

            print("\n=== A probability nobody gave ===")
            card = rows_of(await ex(sophie).preview_change("create_crm_opportunity", {"contact_id": str(marcus.id), "title": "Second account"}))
            check("is shown as not given", card.get("Probability"), "Not given")
            made = await ex(sophie).execute("create_crm_opportunity", {"contact_id": str(marcus.id), "title": "Second account"})
            check("and stored as none", (await s.get(CrmOpportunity, uuid.UUID(made["opportunity_id"]))).probability_pct, None)

            print("\n=== Won is the onboarding case opening ===")
            ask = {"opportunity_id": str(p_deal.id), "registration_type": "individual"}
            card = rows_of(await ex(sophie).preview_change("start_onboarding", ask))
            check("the card takes the contact's name and email and says the deal is won",
                  (card.get("Prospect"), card.get("Email"), card.get("From the pipeline")),
                  ("Priya Nathan", "priya@example.com", "Priya - new relationship: Proposal becomes Won"))
            out = await ex(sophie).execute("start_onboarding", ask)
            case = (await s.execute(select(OnboardingCase).where(OnboardingCase.prospect_name == "Priya Nathan"))).scalar_one()
            check("the case opened with the contact's email, and the deal is won and names it",
                  ((case.intake or {}).get("email"), p_deal.stage, p_deal.onboarding_case_id == case.id, p_deal.closed_at is not None),
                  ("priya@example.com", "won", True, True))
            check("the result says the deal is won", "now won" in out["message"], True)
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "crm.opportunity_won"))).scalars().all()
            check("on the audit log, in the adviser's name", [(a.actor_email, a.subject) for a in audit], [("sophie@nz", "Priya Nathan")])
            await refused("a deal opens one case", ex(sophie).preview_change("start_onboarding", ask), "already opened")
            await refused("a deal of another firm is not found", ToolExecutors(s, sophie.id, sophie.role, other.id).preview_change(
                "start_onboarding", ask), "not this firm")
            await refused("a won deal does not move back", ex(sophie).preview_change("update_crm_opportunity_stage",
                          {"opportunity_id": str(p_deal.id), "stage": "proposal"}), "onboarding case")
            funnel = await pipeline.overview(s, firm, sophie)
            won = next(r for r in funnel["opportunities"] if r["id"] == str(p_deal.id))
            check("the funnel reads the won deal's onboarding state from the board",
                  (won["onboarding"]["state"], won["onboarding"]["step"], funnel["won"]["in_onboarding"], won["client"]), ("draft", 1, 1, False))
            check("and counts it won in the last year", (funnel["last_year"]["won"], funnel["last_year"]["median_days_to_won"] >= 39), (1, True))

            print("\n=== The page opens it the same way ===")
            from app.api import onboarding as onboarding_api
            await onboarding_api.create_case(onboarding_api.CaseCreate(prospect_name="", registration_type="individual",
                                             opportunity_id=str(t_deal.id)), user=tom, firm=firm, db=s)
            check("the route wins Tom's deal with Tara's name", (t_deal.stage, (await s.get(OnboardingCase, t_deal.onboarding_case_id)).prospect_name),
                  ("won", "Tara Quinn"))
            from app.api import crm as crm_api
            await refused("the route will not move a deal to won", crm_api.update_opportunity(m_deal.id, crm_api.OpportunityUpdate(stage="won"),
                          user=sophie, firm=firm, db=s), "start_onboarding")

            print("\n=== Activation carries the deal to the household, and a reversal takes it back ===")
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Priya Nathan", segment="private_wealth", values={})
            s.add(hh)
            await s.flush()
            check("one deal linked", await pipeline.on_activation(s, case, hh.id), 1)
            check("the deal names the client, the contact is a client contact",
                  (p_deal.converted_household_id == hh.id, priya.household_id == hh.id, priya.contact_type), (True, True, "client_contact"))
            await pipeline.on_reversal(s, case)
            check("reversed: no client yet, a prospect again, still won",
                  (p_deal.converted_household_id, priya.household_id, priya.contact_type, p_deal.stage), (None, None, "prospect", "won"))
            await refused("a contact is not made a client by hand", ex(sophie).preview_change("create_crm_contact",
                          {"full_name": "X", "contact_type": "client_contact"}), "only through onboarding")

            print("\n=== Booking a meeting, and the brief ===")
            when = (now + timedelta(days=4)).replace(microsecond=0).isoformat()
            await refused("only a meeting is booked ahead", ex(sophie).preview_change("log_crm_activity",
                          {"contact_id": str(marcus.id), "activity_type": "call", "detail": "x", "occurred_at": when}), "only a meeting")
            booked = await ex(sophie).execute("log_crm_activity", {"contact_id": str(marcus.id), "activity_type": "meeting",
                                                                   "detail": "Review his statements", "occurred_at": when})
            check("a meeting ahead is booked", (booked["booked"], booked["message"].startswith("Booked a meeting")), (True, True))
            brief = await ex(sophie).execute("prepare_prospect_meeting", {"contact_id": str(marcus.id)})
            check("the brief shows the booking and only what happened",
                  (len(brief["booked"]), brief["booked"][0]["detail"], [a["detail"] for a in brief["activity"]][-1]),
                  (1, "Review his statements", "First call"))
            check("and says what is not on file", brief["not_on_file"],
                  ["Email", "Phone", "How they came to the firm", "Assets they would bring", "When they expect to decide"])
            check("the booked meeting is not a touch until it happens",
                  (await pipeline.last_touch(s, firm.id, [m_deal]))[m_deal.id] < now + timedelta(days=1), True)
            await refused("another firm's contact has no brief", ToolExecutors(s, sophie.id, sophie.role, other.id).execute(
                "prepare_prospect_meeting", {"contact_id": str(marcus.id)}), "not this firm")

            print("\n=== The follow-up agent ===")
            from app.agents.pipeline_follow_up import PipelineFollowUpAgent
            from app.atlas.base import AgentContext, Subject
            from app.models.engagement import Task
            quiet_c = contact("Wen Li", tom)
            await s.flush()
            q_deal = deal(quiet_c, "Wen - new relationship", "proposal", 30, estimated_aum=500_000)
            await s.flush()
            agent = PipelineFollowUpAgent()
            ctx = AgentContext(session=s, firm=firm, run=None, tier=agent.default_tier, guardrails={}, config={},
                               subject=Subject("firm", firm.id, firm.name))
            sensed = await agent.sense(ctx)
            drafts = await agent.think(ctx, sensed)
            check("it raises the proposal gone quiet past ten days", [(d.subject.label, d.payload["stage"], d.payload["target_days"]) for d in drafts],
                  [("Wen Li", "proposal", 10)])
            d = drafts[0]

            class Rec:
                subject_id, subject_label, payload, modified_payload, decided_by = q_deal.id, "Wen Li", d.payload, None, lead.id
            before = q_deal.stage
            res = await agent.act(ctx, Rec())
            task = await s.get(Task, uuid.UUID(res["task_id"]))
            check("approving gives Tom, the owner, a task due today and moves nothing",
                  (task.assigned_to == tom.id, "Follow up Wen Li" in task.title, q_deal.stage == before), (True, True, True))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
