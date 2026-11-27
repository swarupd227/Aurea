"""Prove advice under the Investment Adviser operating model (B-399).

  1. The tools: one read that changes nothing, two compliance changes behind a Confirm card, none held by a client.
  2. On the adviser's track a new recommendation, a plan delivery and a plan summary's approval are refused with the
     reason a book-moving approval gives; other tracks and other markets are not held.
  3. Facts are never held: a baseline, a review, what became of earlier advice, and dismissing a proposal.
  4. A recorded recommendation keeps the risk profile it rested on, as on file that day.
  5. Records show an assumed retention period, and nothing deletes one.
  6. The client count stands beside the assumed individual-registration line.
  7. Compliance draws a sample nobody picks and records findings, added and never edited.
  8. The demonstration carries it, once.

Runs against a throwaway SQLite database, with the demonstration firm built from invented prices.

    python -m tests.verify_india_advice
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
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import firm_time, law_figures, plan_inputs, pre_advice, risk_profile
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.consent_registry import ConsentRecord
from app.models.enums import AgentKey, HumanAction, RecommendationStatus, UserRole
from app.models.governance import LedgerEntry, Recommendation
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm
from seed import india_demo

PASS, FAIL = [], []
R = UserRole
ROOT = Path(__file__).resolve().parents[2]


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def section(title: str, fn) -> None:
    """One part's checks. A section that raises (the feature is not there) is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:300]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except (ExecutorError, pre_advice.PreAdviceError) as e:
        check(label, contains.lower() in str(e).lower() or str(e), True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def labels(card: dict) -> dict:
    return {r["label"]: r["value"] for r in card["rows"]}


def advice(household_id, **over) -> dict:
    return {"household_id": str(household_id), "category": "investment", "recommendation": "Move the idle cash into a liquid fund",
            "facts": "Cash above six months of spending, as on file",
            "alternatives": [{"option": "Leave it", "why_rejected": "It earns less than the fund"}], **over}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    real_missing = plan_inputs.missing_of
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            sharma, banerjee, iyer = homes["The Sharma Family"], homes["The Banerjee Household"], homes["The Iyer Family"]
            today = firm_time.today(firm)

            async def person_of(email) -> User:
                return (await s.execute(select(User).where(User.email == email))).scalar_one()

            users = {role: await person_of(f"{who}@astra-india.demo") for role, who in (
                (R.ADVISER, "adviser"), (R.PARAPLANNER, "paraplanner"), (R.COMPLIANCE, "compliance"), (R.ADMIN, "admin"))}

            def ex(role) -> ToolExecutors:
                return ToolExecutors(s, users[role].id, role, firm.id)

            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            s.add(us)
            await s.flush()
            amy = User(id=uuid.uuid4(), firm_id=us.id, email="a@us.test", full_name="Amy", hashed_password="x", role=R.ADVISER)
            cole = User(id=uuid.uuid4(), firm_id=us.id, email="c@us.test", full_name="Cole", hashed_password="x", role=R.COMPLIANCE)
            smiths = Household(id=uuid.uuid4(), firm_id=us.id, name="The Smiths")
            s.add_all([amy, cole, smiths])
            await s.flush()

            async def tools():
                check("read_advice_audit changes nothing", TOOLS["read_advice_audit"].change_state, ToolChangeState.NO)
                for key in ("draw_advice_audit", "record_advice_audit"):
                    check(f"{key} waits for Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                    check(f"{key} is compliance's alone", TOOLS[key].roles_required, {R.COMPLIANCE})
                check("no client holds any of them",
                      [k for k in ("read_advice_audit", "draw_advice_audit", "record_advice_audit") if R.CLIENT in TOOLS[k].roles_required], [])
                for key in ("record_recommendation", "record_plan_delivery"):
                    check(f"{key} says it is held before advice", "refused while the client's pre-advice paperwork" in TOOLS[key].description, True)
                renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
                panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
                check("each has a card and opens beside the conversation",
                      [k for k in ("read_advice_audit", "draw_advice_audit", "record_advice_audit")
                       if f'case "{k}":' not in renderer or f'case "{k}":' not in panes], [])

            await section("The tools", tools)

            async def gate():
                why = (await pre_advice.household_refusal(s, firm, banerjee.id))
                check("the Banerjees are held, in the book-moving approval's own words",
                      why, pre_advice.refusal_text(await pre_advice.status(s, firm, banerjee.id)))
                await refused("a recommendation for them offers no card",
                              ex(R.ADVISER).preview_change("record_recommendation", advice(banerjee.id)), "before the client's paperwork")
                await refused("and is not recorded", ex(R.ADVISER).record_recommendation(advice(banerjee.id)), "Risk profile complete and consented")
                row = await ex(R.ADVISER).record_recommendation(advice(iyer.id))
                check("a Portfolio Manager's household is not held", row["status"], "open")
                row = await ex(R.ADVISER).record_recommendation(advice(sharma.id))
                check("a household with its paperwork is not held", row["status"], "open")
                other = await ToolExecutors(s, amy.id, R.ADVISER, us.id).record_recommendation(advice(smiths.id))
                check("another market's household is not held", other["status"], "open")
                mine = await ex(R.ADVISER).read_advice_file({"household_id": str(banerjee.id)})
                check("their advice file shows the hold and what is missing",
                      (mine["pre_advice"]["met"], mine["pre_advice"]["missing"]), (False, ["Risk profile complete and consented"]))
                check("and the message says so", "No new advice may be recorded" in mine["message"], True)
                book = await ex(R.COMPLIANCE).read_advice_file({})
                check("the firm view names the held households", "The Banerjee Household" in [h["household"] for h in book["held"]], True)
                check("another market shows no gate",
                      (await ToolExecutors(s, amy.id, R.ADVISER, us.id).read_advice_file({"household_id": str(smiths.id)}))["pre_advice"], None)

            await section("Recorded advice waits for the paperwork", gate)

            async def plans():
                async def complete(session, household_id, *, firm_id):
                    return 4, []

                plan_inputs.missing_of = complete
                for home in (banerjee, iyer):
                    await ex(R.ADVISER).record_plan_baseline({"household_id": str(home.id)})
                    await ex(R.PARAPLANNER).record_plan_review({"household_id": str(home.id), "outcome": "passed"})
                held = await ex(R.ADVISER).read_plan_record({"household_id": str(banerjee.id)})
                check("a baseline and its review are recorded for a held household", held["state"], "reviewed")
                check("the plan record shows the hold", (held["pre_advice"]["met"], "Neither a delivery" in held["message"]), (False, True))
                delivery = {"household_id": str(banerjee.id), "evidence_kind": "note", "evidence": "Meeting note of today's review"}
                await refused("a delivery offers no card", ex(R.ADVISER).preview_change("record_plan_delivery", delivery), "before the client's paperwork")
                await refused("and is not recorded", ex(R.ADVISER).record_plan_delivery(delivery), "Risk profile complete and consented")
                done = await ex(R.ADVISER).record_plan_delivery({**delivery, "household_id": str(iyer.id)})
                check("a Portfolio Manager's household takes its delivery", (done["state"], done["pre_advice"]), ("delivered", None))
                firm_view = await ex(R.COMPLIANCE).read_plan_record({})
                check("the firm's plans flag the held household",
                      {r["household"]: r["advice_held"] for r in firm_view["households"] if r["household"] in (banerjee.name, iyer.name)},
                      {banerjee.name: True, iyer.name: False})

                await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PLAN_SUMMARY, trigger="test",
                                        subject=Subject("household", banerjee.id, banerjee.name))
                rec = (await s.execute(select(Recommendation).where(
                    Recommendation.firm_id == firm.id, Recommendation.agent_key == AgentKey.PLAN_SUMMARY.value,
                    Recommendation.subject_id == banerjee.id))).scalars().one()
                decider = await s.get(User, uuid.UUID(rec.payload["adviser_id"])) if rec.payload.get("adviser_id") else users[R.ADVISER]
                tool = ToolExecutors(s, decider.id, R.ADVISER, firm.id)
                await refused("publishing the summary offers no card",
                              tool.preview_change("decide_recommendation", {"recommendation_id": str(rec.id), "action": "approve"}),
                              "before the client's paperwork")
                try:
                    await runtime.decide(s, firm=firm, recommendation=rec, action=HumanAction.APPROVE, actor_id=decider.id,
                                         actor_label=decider.email)
                    check("approving it is refused", "approved", "refused")
                except pre_advice.PreAdviceError as exc:
                    check("approving it is refused, naming what is missing", "Risk profile complete and consented" in str(exc), True)
                await s.refresh(rec)
                check("and nothing was decided", (rec.status, rec.decided_by), (RecommendationStatus.PROPOSED, None))

                s.add(ConsentRecord(firm_id=firm.id, household_id=banerjee.id, kind="risk_profile", status="granted",
                                    given_by="Subir Banerjee", given_on=today, method="email",
                                    evidence="Reply from the registered email (test)", recorded_by="test"))
                await s.flush()
                check("once the consent is recorded nothing is held", await pre_advice.household_refusal(s, firm, banerjee.id), None)
                await runtime.decide(s, firm=firm, recommendation=rec, action=HumanAction.APPROVE, actor_id=decider.id,
                                     actor_label=decider.email)
                latest = (await ex(R.ADVISER).read_plan_record({"household_id": str(banerjee.id)}))["summary"]
                check("the summary is then published", latest["with_client"], True)
                done = await ex(R.ADVISER).record_plan_delivery(delivery)
                check("and the delivery recorded", done["state"], "delivered")
                outcome_on = await ex(R.ADVISER).record_recommendation(advice(banerjee.id))
                s.add(ConsentRecord(firm_id=firm.id, household_id=banerjee.id, kind="risk_profile", status="withdrawn",
                                    given_by="Subir Banerjee", given_on=today, method="email",
                                    evidence="Withdrawn by email (test)", recorded_by="test",
                                    created_at=__import__("app.core.db", fromlist=["utcnow"]).utcnow() + timedelta(seconds=5)))
                await s.flush()
                await refused("with the consent withdrawn, new advice is held again",
                              ex(R.ADVISER).record_recommendation(advice(banerjee.id)), "before the client's paperwork")
                said = await ex(R.ADVISER).record_advice_outcome({"recommendation_id": outcome_on["id"], "status": "declined",
                                                                  "evidence_kind": "note", "evidence": "Meeting note: declined"})
                check("what became of earlier advice is still recorded", said["status"], "declined")

            try:
                await section("A plan's delivery and summary wait, a baseline does not", plans)
            finally:
                plan_inputs.missing_of = real_missing

            async def basis():
                card = labels(await ex(R.ADVISER).preview_change("record_recommendation", advice(sharma.id)))
                check("the card names the profile it rests on", "balanced (household)" in card.get("Risk profile it rests on", ""), True)
                check("  and how long it is kept", card.get("Kept"), "At least 5 years (assumed, not reviewed)")
                row = await ex(R.ADVISER).record_recommendation(advice(sharma.id))
                b = row["risk_basis"]
                check("the record keeps it", (b["status"], b["of"], b["indicated"], b["answered_on"], b["consent"]["method"]),
                      ("on_file", "household", "balanced", today.isoformat(), "email"))
                early = await ex(R.ADVISER).record_recommendation(advice(sharma.id, recommended_on=(today - timedelta(days=1)).isoformat()))
                check("advice made before any questionnaire reads not on file", early["risk_basis"]["status"], "not_on_file")
                rajesh = (await s.execute(select(Person).where(Person.household_id == sharma.id, Person.full_name == "Rajesh Sharma"))).scalar_one()
                cautious = {q["key"]: q["options"][0] for q in risk_profile.QUESTIONS}
                await risk_profile.submit(s, sharma.id, cautious, firm_id=firm.id, by="test", role="adviser", subject_id=rajesh.id)
                own = await ex(R.ADVISER).record_recommendation(advice(sharma.id, person_id=str(rajesh.id)))
                check("a person asked for themselves is read on their own answers",
                      (own["risk_basis"]["of"], own["risk_basis"]["indicated"], own["risk_basis"]["consent"]), ("Rajesh Sharma", "conservative", None))
                again = await ex(R.ADVISER).read_advice_file({"household_id": str(sharma.id)})
                kept = next(r for r in again["recommendations"] if r["id"] == row["id"])
                check("a later questionnaire changes no record", kept["risk_basis"], b)
                other = await ToolExecutors(s, amy.id, R.ADVISER, us.id).record_recommendation(advice(smiths.id))
                check("with no questionnaire, not on file", other["risk_basis"]["status"], "not_on_file")

            await section("The risk profile on the record", basis)

            async def kept():
                fig = law_figures.get("in_ia_record_retention_years")
                check("the retention period is a law figure marked assumed", (fig.value, fig.source.startswith("ASSUMED")), (5, True))
                view = await ex(R.ADVISER).read_advice_file({"household_id": str(sharma.id)})
                check("shown beside the records as assumed, deleting nothing",
                      (view["retention"]["years"], view["retention"]["assumed"], view["retention"]["deletes"]), (5, True, False))
                r = view["recommendations"][0]
                latest = date.fromisoformat(max(r["recommended_on"], r["status_on"]))
                check("each record says how long it is kept", r["keep_until"], latest.replace(year=latest.year + 5).isoformat())
                theirs = await ToolExecutors(s, amy.id, R.ADVISER, us.id).read_advice_file({"household_id": str(smiths.id)})
                check("another market gets no period", (theirs["retention"], theirs["recommendations"][0]["keep_until"]), (None, None))
                check("counsel's tracker lists it", "in_ia_record_retention_years" in
                      __import__("app.aurea_core.counsel_review", fromlist=["x"]).india_keys(), True)

            await section("Retention", kept)

            async def line():
                v = await ex(R.COMPLIANCE).execute("read_advice_audit", {})
                c = v["capacity"]
                check("the adviser's clients are counted against the assumed line",
                      (c["line"], c["clients"] >= c["households"] >= 3, c["state"], c["assumed"], c["registration"]),
                      (300, True, "within", True, None))
                check("the figure carries counsel's standing", c["figure"]["counsel"]["state"] if "counsel" in c["figure"] else "none",
                      "not_reviewed")
                await refused("a registration type is individual or non-individual",
                              ex(R.ADMIN).preview_change("record_sebi_registration", {"ia_registration_type": "both", "reason": "certificate"}),
                              "individual or non-individual")
                await ex(R.ADMIN).record_sebi_registration({"ia_registration_type": "non-individual", "reason": "registration certificate"})
                v = await ex(R.COMPLIANCE).read_advice_audit({})
                check("a non-individual registration is not measured against it",
                      (v["capacity"]["registration"], v["capacity"]["state"]), ("non_individual", "not_applicable"))
                check("another market has no operating model",
                      (await ToolExecutors(s, cole.id, R.COMPLIANCE, us.id).read_advice_audit({}))["applies"], False)

            await section("The registration line", line)

            async def audit():
                await refused("an adviser draws no sample", ex(R.ADVISER).preview_change("draw_advice_audit", {}), "compliance")
                await refused("nor does an adviser record a result",
                              ex(R.ADVISER).record_advice_audit({"recommendation_id": str(uuid.uuid4()), "outcome": "met"}), "compliance")
                before = await ex(R.COMPLIANCE).read_advice_audit({})
                check("the demonstration's sample carries one finding",
                      (before["metrics"]["audits"], before["metrics"]["reviewed"], before["metrics"]["with_findings"], before["metrics"]["awaiting"]),
                      (1, 1, 1, 1))
                card = labels(await ex(R.COMPLIANCE).preview_change("draw_advice_audit", {"size": 3}))
                check("the card says how many, and that nobody picks", (card["Files to draw"], "nobody" in card["Chosen"]), ("3", True) if isinstance(card["Files to draw"], str) else (3, True))
                drawn = await ex(R.COMPLIANCE).draw_advice_audit({"size": 3})
                new = drawn["audits"][0]
                check("a second sample is numbered and drawn", (new["seq"], new["drawn"], new["reviewed"]), (2, 3, 0))
                old = {f["recommendation_id"] for f in drawn["audits"][1]["files"]}
                check("files never drawn come first", [f for f in new["files"] if f["recommendation_id"] in old], [])
                f = new["files"][0]
                check("each file shows what its record holds",
                      sorted(f["checks"]), ["alternatives", "consented_when_advised", "decision", "evidence_kind", "paperwork_today", "risk_profile"])
                await refused("a finding needs words", ex(R.COMPLIANCE).record_advice_audit(
                    {"recommendation_id": f["recommendation_id"], "outcome": "finding", "findings": [{"item": "alternatives", "words": "thin"}]}),
                    "in the reviewer's words")
                await refused("a finding result names one", ex(R.COMPLIANCE).record_advice_audit(
                    {"recommendation_id": f["recommendation_id"], "outcome": "finding"}), "names it")
                await refused("an undrawn file takes no result", ex(R.COMPLIANCE).record_advice_audit(
                    {"recommendation_id": str(uuid.uuid4()), "outcome": "met"}), "not drawn")
                ready = {"recommendation_id": f["recommendation_id"], "outcome": "finding",
                         "findings": [{"item": "alternatives", "words": "Only one alternative, with no figures"}]}
                card = labels(await ex(R.COMPLIANCE).preview_change("record_advice_audit", ready))
                check("the card names the client and the finding", ("Only one alternative" in card["Findings"], bool(card["Client"])), (True, True))
                done = await ex(R.COMPLIANCE).record_advice_audit(ready)
                check("counted", (done["metrics"]["reviewed"], done["metrics"]["with_findings"], done["metrics"]["by_item"]["alternatives"]), (2, 2, 1))
                await refused("a result is never recorded twice", ex(R.COMPLIANCE).record_advice_audit(ready), "already recorded")
                await refused("more than the period holds is refused", ex(R.COMPLIANCE).draw_advice_audit({"size": 500}), "between 1 and")
                events = (await s.execute(select(LedgerEntry.event_type).where(
                    LedgerEntry.firm_id == firm.id, LedgerEntry.event_type.like("advice_audit%")))).scalars().all()
                check("on the ledger", sorted(set(events)), ["advice_audit_drawn", "advice_audit_result"])

            await section("The sample audit", audit)

            async def demonstration():
                from seed import india_advice

                from app.models.advice_audit import AdviceAudit
                from app.models.advice_file import AdviceRecommendation

                seeded = (await s.execute(select(AdviceRecommendation).where(AdviceRecommendation.recorded_by == india_advice.BY))).scalars().all()
                check("three demonstration recommendations, none for the held household",
                      (len(seeded), banerjee.id in {r.household_id for r in seeded}), (3, False))
                count = select(func.count()).select_from(AdviceAudit)
                was = (await s.execute(count)).scalar_one()
                check("a second run adds nothing", (await india_advice.add(s, firm), (await s.execute(count)).scalar_one()), (0, was))

            await section("The demonstration", demonstration)
    finally:
        plan_inputs.missing_of = real_missing
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  failed: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
