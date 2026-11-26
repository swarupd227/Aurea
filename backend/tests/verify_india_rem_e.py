"""Prove India batch E: what a signed agreement carried (B-324), a client's own language or WhatsApp request (B-322), the firm's mutual fund cut-off
figures (B-330), counsel's determination of the family of entities (B-331), and the advertisement approval a promotional text cites (B-332).

Covers the pre-advice gate failing an agreement whose Annexure A or MITC is not recorded and naming it, an eSign envelope recording only what its
template declared; a client's request becoming a task for the recorded adviser and changing nothing; a figure assumed until recorded and the NAV check
reading the recorded one; a group entity flagged when within or awaiting counsel and not when outside; a promotional message, report refused without an
approval for exactly its words; every refusal; the tools, their cards and the routes.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_rem_e
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import canvas
from app.aurea_core import (ad_approval, advertising, advisers, agreement_contents, client_channel_request, client_language, distribution_separation as ds,
                            firm_time, india_stack, mf_figures, mf_orders, pre_advice, reports)
from app.core.db import Base
from app.core.security import UserRole
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.consent_registry import ConsentRecord
from app.models.engagement import ClientReport, Task
from app.models.enums import ReportStatus
from app.models.graph import Household, Person
from app.models.identity import AuditEvent, User
from app.models.india_rem_e import AdCitation, AgreementContent, MfFigure
from app.models.tenant import Firm
from app.simulations import india_stack as sim_stack
from app.simulations import opt_in
from seed import india_demo, india_rem_e

PASS, FAIL = [], []
ERRORS = (agreement_contents.AgreementContentError, mf_figures.FigureError, ds.SeparationError, ad_approval.AdApprovalError,
          client_channel_request.ChannelRequestError, india_stack.StackError, ExecutorError)
NEW_TOOLS = ("read_mf_figures", "record_mf_figure", "read_agreement_contents", "record_agreement_contents", "record_family_determination", "cite_report_ad_approval")
WED = date(2026, 11, 25)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ERRORS as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:140]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def card(c: dict) -> dict:
    return {r["label"]: r["value"] for r in c["rows"]}


class NoIncludes:
    """A provider whose template declares nothing about what it included."""

    key = "stub_noinc"
    name = "Stub eSign (declares nothing)"
    simulated = False

    def enabled_for(self, firm) -> bool:
        return True

    async def send_for_signature(self, session, firm, person, document_name: str) -> dict:
        return {"reference": "STUB-ESIGN-0001", "state": "signed", "signed_on": firm_time.today(firm)}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            adviser, compliance, ops, research = users[UserRole.ADVISER], users[UserRole.COMPLIANCE], users[UserRole.OPERATIONS], users[UserRole.RESEARCH_CIO]
            adv = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            comp = ToolExecutors(s, compliance.id, UserRole.COMPLIANCE, firm.id)
            opx = ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id)
            sharma, dsouza, banerjee = homes["The Sharma Family"], homes["The D'Souza Household"], homes["The Banerjee Household"]

            print("\n=== B-324: the seed, and the gate reading what an agreement carried ===")
            st = await pre_advice.status(s, firm, sharma.id)
            agr = next(c for c in st["checks"] if c["key"] == "agreement")
            check("the seeded agreements are recorded as carrying both, and the gate passes", (agr["state"], st["met"], "Annexure A and the MITC recorded" in agr["detail"]), ("pass", True, True))
            check("the seed recorded one row for each gated household whose agreement is on file", (await s.execute(select(func.count()).select_from(AgreementContent))).scalar_one() >= 4, True)
            await s.execute(AgreementContent.__table__.delete().where(AgreementContent.household_id == sharma.id))
            await s.flush()
            st = await pre_advice.status(s, firm, sharma.id)
            agr = next(c for c in st["checks"] if c["key"] == "agreement")
            check("with no record, an agreement on file fails, naming both parts", (agr["state"], "agreement incomplete: Annexure A / MITC not recorded" in agr["detail"], st["met"]), ("fail", True, False))
            check("and the gate's missing list names the check and the parts", any(m.startswith("Agreement signed, with Annexure A and the MITC (agreement incomplete: Annexure A / MITC not recorded") for m in st["missing"]), True)
            refusal = pre_advice.refusal_text(st)
            check("the refusal text carries what is missing", "Annexure A / MITC not recorded" in refusal, True)

            print("\n=== B-324: recording it, and a correction ===")
            await refused("a role outside the recorders is refused", agreement_contents.prepare(s, firm, UserRole.RESEARCH_CIO, household_id=sharma.id, annexure_a=True, mitc=True, document="Agreement, filed"), "adviser, a paraplanner")
            await refused("each part must be answered", agreement_contents.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, annexure_a=True, mitc=None, document="Agreement, filed"), "MITC")
            await refused("the document must be named", agreement_contents.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, annexure_a=True, mitc=True, document=""), "filed")
            await refused("a filing note longer than its column is refused", agreement_contents.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, annexure_a=True, mitc=True, document="x" * 301), "300 characters")
            await refused("another firm's household is not found", agreement_contents.prepare(s, firm, UserRole.ADVISER, household_id="00000000-0000-0000-0000-000000000000", annexure_a=True, mitc=True, document="Agreement, filed"), "no such household")
            await refused("a firm outside India is refused", agreement_contents.prepare(s, Firm(jurisdiction="NZ", settings={}), UserRole.ADVISER, household_id=sharma.id, annexure_a=True, mitc=True, document="Agreement, filed"), "Indian licence")
            c = card(await adv.preview_change("record_agreement_contents", {"household_id": str(sharma.id), "annexure_a": True, "mitc": False, "document": "Signed agreement, filed"}))
            check("the card shows both answers and what it replaces", (c["Carries Annexure A"], c["Carries the MITC"], c["Replaces"]), ("Yes", "No", "Nothing on file"))
            await adv.execute("record_agreement_contents", {"household_id": str(sharma.id), "annexure_a": True, "mitc": False, "document": "Signed agreement, filed"})
            agr = next(c for c in (await pre_advice.status(s, firm, sharma.id))["checks"] if c["key"] == "agreement")
            check("Annexure A alone: the gate names the MITC", (agr["state"], "agreement incomplete: MITC not recorded" in agr["detail"]), ("fail", True))
            await refused("a record already on file is corrected with a reason", agreement_contents.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, annexure_a=True, mitc=True, document="Signed agreement, filed"), "why")
            await adv.execute("record_agreement_contents", {"household_id": str(sharma.id), "annexure_a": False, "mitc": True, "document": "Signed agreement, filed", "reason": "Re-read the signed copy"})
            agr = next(c for c in (await pre_advice.status(s, firm, sharma.id))["checks"] if c["key"] == "agreement")
            check("the MITC alone: the gate names Annexure A", (agr["state"], "agreement incomplete: Annexure A not recorded" in agr["detail"]), ("fail", True))
            await adv.execute("record_agreement_contents", {"household_id": str(sharma.id), "annexure_a": True, "mitc": True, "document": "Signed agreement, filed", "reason": "The signed copy carries both"})
            st = await pre_advice.status(s, firm, sharma.id)
            check("both recorded: the gate passes and the earlier rows stay", (st["met"], (await s.execute(select(func.count()).select_from(AgreementContent).where(AgreementContent.household_id == sharma.id))).scalar_one()), (True, 3))
            read = await adv.execute("read_agreement_contents", {"household_id": str(sharma.id)})
            check("the read says what is recorded, and not that the agreement meets any rule", (read["annexure_a"], read["mitc"], "does not mean" in read["message"] or "carried" in read["message"]), (True, True, True))

            print("\n=== B-324: an eSign envelope records only what its template declared ===")
            india_stack.register(sim_stack.PROVIDER)
            await opt_in(s, firm, sim_stack.KEY)
            karthik = people["Karthik Iyer"]
            await india_stack.send_for_esign(s, firm, UserRole.ADVISER, person_id=karthik.id, document_name=None, by="t")
            item = next(i for i in (await india_stack.overview(s, firm))["items"] if i["household"] == karthik.household_id and i["kind"] == "esign") if False else None
            from app.models.india_batch_b import StackItem

            env = (await s.execute(select(StackItem).where(StackItem.person_id == karthik.id, StackItem.kind == "esign", StackItem.of_item_id.is_(None)))).scalars().first()
            check("the simulated template declares what it included, kept as returned", env.detail, {"includes": ["annexure_a", "mitc"]})
            out = await india_stack.record_agreement(s, firm, UserRole.ADVISER, by="t", item_id=env.id)
            row = await agreement_contents.standing(s, firm.id, karthik.household_id)
            check("confirming it records both from the declaration, as eSign", (out["contents"], row.source, row.annexure_a, row.mitc), ("declared by the template", "esign", True, True))
            real_provider_for = india_stack.provider_for
            india_stack.provider_for = lambda f: NoIncludes()
            try:
                await india_stack.send_for_esign(s, firm, UserRole.ADVISER, person_id=people["Elaine D'Souza"].id, document_name=None, by="t")
            finally:
                india_stack.provider_for = real_provider_for
            env2 = (await s.execute(select(StackItem).where(StackItem.reference == "STUB-ESIGN-0001"))).scalars().first()
            check("an envelope that declares nothing keeps no declaration", env2.detail, None)
            before = (await s.execute(select(func.count()).select_from(AgreementContent).where(AgreementContent.household_id == dsouza.id))).scalar_one()
            out = await india_stack.record_agreement(s, firm, UserRole.ADVISER, by="t", item_id=env2.id)
            after = (await s.execute(select(func.count()).select_from(AgreementContent).where(AgreementContent.household_id == dsouza.id))).scalar_one()
            check("and records nothing about its contents: never assumed", (before, after, out["contents"]), (before, before, "not declared by the envelope: not recorded"))

            print("\n=== B-322: a client asks for a language or WhatsApp on their own page ===")
            from app.aurea_core import whatsapp as wa_core

            bare = None
            for h in homes.values():
                if await client_language.standing(s, firm.id, h.id) is None and await wa_core.opt_in(s, firm.id, h.id) is None and (await advisers.adviser_of(s, firm.id, h.id)) is not None:
                    bare = h
                    break
            assert bare is not None, "the demonstration firm has a household with no language and no WhatsApp on file"
            banerjee = bare
            subir = (await s.execute(select(Person).where(Person.household_id == bare.id).order_by(Person.full_name))).scalars().first()
            client = User(firm_id=firm.id, email="subir.client@astra-india.demo", full_name="Subir Banerjee", hashed_password="x", role=UserRole.CLIENT, person_id=subir.id)
            s.add(client)
            await s.flush()
            seen = await canvas.canvas_channels(household_id=None, user=client, firm=firm, db=s)
            check("the client sees the languages they may ask for, and none recorded", (seen["applies"], seen["language"], seen["language_recorded"], len(seen["languages"])), (True, "en", False, 8))
            made = await canvas.submit_channel_request(canvas.ChannelRequestIn(kind="language", language="Hindi"), user=client, firm=firm, db=s)
            task = await s.get(Task, __import__("uuid").UUID(made["task_id"]))
            owner = await advisers.adviser_of(s, firm.id, banerjee.id)
            check("the request is a task for the recorded adviser", (task.source, task.household_id, task.assigned_to, task.title), ("client_request", banerjee.id, owner.id if owner else None, "Client asks to be written to in Hindi"))
            check("and records nothing: no language, no consent", (await client_language.standing(s, firm.id, banerjee.id), made["recorded"],
                                                                  (await s.execute(select(func.count()).select_from(ConsentRecord).where(ConsentRecord.household_id == banerjee.id, ConsentRecord.kind == "whatsapp"))).scalar_one()), (None, False, 0))
            check("the task tells the person what to record, and that nothing is changed until then", ("record_client_language" in task.detail, "Nothing is changed until a person records it" in task.detail), (True, True))
            try:
                await canvas.submit_channel_request(canvas.ChannelRequestIn(kind="language", language="Hindi"), user=client, firm=firm, db=s)
                check("asking twice is refused", "accepted", "refused")
            except HTTPException as exc:
                check("asking twice is refused", (exc.status_code, "already asked" in exc.detail), (422, True))
            try:
                await canvas.submit_channel_request(canvas.ChannelRequestIn(kind="language", language="Klingon"), user=client, firm=firm, db=s)
                check("a language outside the list is refused", "accepted", "refused")
            except HTTPException as exc:
                check("a language outside the list is refused", exc.status_code, 422)
            wa = await canvas.submit_channel_request(canvas.ChannelRequestIn(kind="whatsapp"), user=client, firm=firm, db=s)
            check("a WhatsApp request is a task too, and not an opt-in", ((await s.get(Task, __import__("uuid").UUID(wa["task_id"]))).title, await __import__("app.aurea_core.whatsapp", fromlist=["opt_in"]).opt_in(s, firm.id, banerjee.id)), ("Client asks for WhatsApp", None))
            seen = await canvas.canvas_channels(household_id=None, user=client, firm=firm, db=s)
            check("the page shows both as sent", seen["open"], {"language": True, "whatsapp": True})
            try:
                await canvas.submit_channel_request(canvas.ChannelRequestIn(kind="language", language="Hindi"), user=adviser, firm=firm, db=s)
                check("staff do not ask for a client", "accepted", "refused")
            except HTTPException as exc:
                check("staff do not ask for a client", exc.status_code, 403)
            check("no tool of this batch is open to a client, and no tool makes the request", (all(UserRole.CLIENT not in TOOLS[k].roles_required for k in NEW_TOOLS), "request_language" in TOOLS), (True, False))
            recorded = await adv.execute("record_client_language", {"household_id": str(banerjee.id), "language": "Hindi", "asked_by": "Subir Banerjee",
                                                                  "evidence": "The client's own request on their Canvas page, as the open task shows"})
            check("a person records it through the existing tool, and then it stands", (await client_language.standing(s, firm.id, banerjee.id)).language, "hi")
            check("the client's page then reads Hindi from what is recorded", (await client_channel_request.view(s, firm, banerjee.id))["language_label"], "Hindi")
            check("a firm outside India has no such request", (await client_channel_request.view(s, Firm(jurisdiction="NZ", settings={}), banerjee.id))["applies"], False)

            print("\n=== B-330: the cut-off figures, assumed until recorded ===")
            fs = await mf_figures.overview(s, firm)
            states = {f["key"]: f["state"] for f in fs["figures"]}
            check("the seed recorded one figure and left three assumed", (states["in_mf_cutoff_standard"], states["in_mf_large_amount"], fs["recorded"], fs["assumed"]), ("recorded", "assumed", 1, 3))
            check("a recorded figure reads 'recorded: <circular>' and an assumed one 'assumed'", (next(f for f in fs["figures"] if f["key"] == "in_mf_cutoff_standard")["state_label"].startswith("recorded: "),
                                                                                                 next(f for f in fs["figures"] if f["key"] == "in_mf_large_amount")["state_label"]), (True, "assumed"))
            ov = await mf_orders.overview(s, firm)
            breaks_before = ov["breaks"]
            states_before = sorted((r["order_id"], r["state"]) for r in ov["orders"])
            check("the orders overview carries the figure set", ov["figure_set"]["recorded"], 1)
            figs_default = {}
            base = dict(side="buy", rule="standard", order_on=WED, order_time="14:30", amount=10_000.0, funds_realised_on=None, today=WED)
            check("with no recorded figure 14:30 is in time (assumed 3:00 pm)", mf_orders.nav_date_for(**base)["nav_date"], WED)
            check("with a recorded 14:00 it is after the cut-off", mf_orders.nav_date_for(**base, figures={"in_mf_cutoff_standard": 840})["nav_date"], WED + timedelta(days=1))
            check("a recorded large-purchase line of Rs 1 lakh makes a Rs 150,000 purchase need realisation",
                  (mf_orders.nav_date_for(**{**base, "amount": 150_000.0})["state"], mf_orders.nav_date_for(**{**base, "amount": 150_000.0}, figures={"in_mf_large_amount": 100_000})["state"]), ("assessed", "not_assessed"))
            await refused("a role outside compliance and operations is refused", mf_figures.prepare(s, firm, UserRole.ADVISER, figure_key="in_mf_cutoff_standard", value="14:00", source="SEBI circular dated 1 Jan", as_of=today, reason="A new reading"), "compliance or operations")
            await refused("an unknown figure is refused", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_other", value="14:00", source="SEBI circular dated 1 Jan", as_of=today, reason="A new reading"), "one of")
            await refused("a cut-off must be a time", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="25:99", source="SEBI circular dated 1 Jan", as_of=today, reason="A new reading"), "within the day")
            await refused("the circular must be named", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="14:00", source="", as_of=today, reason="A new reading"), "circular")
            await refused("a circular's name longer than its column is refused", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="14:00", source="x" * 301, as_of=today, reason="A new reading"), "300 characters")
            await refused("a reason is needed", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="14:00", source="SEBI circular dated 1 Jan", as_of=today, reason=""), "reason")
            await refused("the circular cannot be dated ahead", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="14:00", source="SEBI circular dated 1 Jan", as_of=today + timedelta(days=1), reason="A new reading"), "ahead")
            await refused("recording what is already recorded changes nothing", mf_figures.prepare(s, firm, UserRole.COMPLIANCE, figure_key="in_mf_cutoff_standard", value="15:00", source="SEBI circular dated 1 Jan", as_of=today, reason="Again"), "nothing changes")
            c = card(await comp.preview_change("record_mf_figure", {"figure_key": "in_mf_cutoff_standard", "value": "10:00", "source": "SEBI circular dated 1 Jan (invented)", "as_of": today.isoformat(), "reason": "Testing the check"}))
            check("the card shows now, was and the audit note", (c["Now"], c["Was"].endswith("(recorded)"), "audit log" in c["Note"]), ("10:00", True, True))
            audits = (await s.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "mf_figure.recorded"))).scalar_one()
            await comp.execute("record_mf_figure", {"figure_key": "in_mf_cutoff_standard", "value": "10:00", "source": "SEBI circular dated 1 Jan (invented)", "as_of": today.isoformat(), "reason": "Testing the check"})
            check("recording it is on the audit log with its reason", (await s.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "mf_figure.recorded"))).scalar_one(), audits + 1)
            ov = await mf_orders.overview(s, firm)
            check("the NAV-date check now reads the firm's cut-off: what it reports for the same orders changes", sorted((r["order_id"], r["state"]) for r in ov["orders"]) != states_before, True)
            await comp.execute("record_mf_figure", {"figure_key": "in_mf_cutoff_standard", "value": "15:00", "source": "SEBI circular dated 1 Jan (invented)", "as_of": today.isoformat(), "reason": "Back to the assumed time"})
            check("recording it back returns the earlier reading, and nothing was written to an order",
                  (sorted((r["order_id"], r["state"]) for r in (await mf_orders.overview(s, firm))["orders"]) == states_before, (await mf_orders.overview(s, firm))["breaks"],
                   (await s.execute(select(func.count()).select_from(MfFigure))).scalar_one()), (True, breaks_before, 3))
            check("the read tool says what is assumed and what is recorded", "still assumed" in (await adv.execute("read_mf_figures", {}))["message"], True)

            print("\n=== B-331: counsel's determination of the family of entities ===")
            chk = await ds.check(s, firm)
            sub = next(f for f in chk["flags"] if f["entity"] == india_rem_e.SUBSIDIARY)
            check("an entity with no determination is flagged and shown awaiting counsel", (sub["determination"], sub["determination_label"]), ("awaiting_counsel", "Awaiting counsel"))
            check("the entity counsel determined outside is not flagged but listed", (all(f["entity"] != india_rem_e.ASSOCIATE for f in chk["flags"]),
                                                                                       [f["entity"] for f in chk["determined_outside"]] if chk["determined_outside"] else "none recorded"),
                  (True, [india_rem_e.ASSOCIATE] if chk["determined_outside"] else "none recorded"))
            await refused("an adviser does not record counsel's determination", ds.prepare_determination(s, firm, UserRole.ADVISER, entity=india_rem_e.SUBSIDIARY, determination="within", counsel_name="Counsel", determined_on=today, document="Opinion, filed"), "compliance or a branch leader")
            await refused("an entity not on file is refused", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity="Nobody Ltd", determination="within", counsel_name="Counsel", determined_on=today, document="Opinion, filed"), "not on file")
            await refused("the determination is within or outside", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="maybe", counsel_name="Counsel", determined_on=today, document="Opinion, filed"), "within or outside")
            await refused("counsel is named", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="within", counsel_name="", determined_on=today, document="Opinion, filed"), "counsel")
            await refused("counsel's name longer than its column is refused", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="within", counsel_name="x" * 201, determined_on=today, document="Opinion, filed"), "200 characters")
            await refused("the document is named", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="within", counsel_name="Counsel", determined_on=today, document=""), "filed")
            await refused("a determination cannot be dated ahead", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="within", counsel_name="Counsel", determined_on=today + timedelta(days=1), document="Opinion, filed"), "ahead")
            terms = {"entity": india_rem_e.SUBSIDIARY, "determination": "within", "counsel_name": "Demonstration Counsel (invented)", "determined_on": (today - timedelta(days=5)).isoformat(), "document": "Counsel's opinion, filed"}
            c = card(await comp.preview_change("record_family_determination", terms))
            check("the card says it replaces nothing and that it is counsel's", (c["Counsel's determination"], c["Replaces"].startswith("Nothing"), "Counsel's" in c["Note"]), ("Within the family of entities", True, True))
            await comp.execute("record_family_determination", terms)
            chk = await ds.check(s, firm)
            sub = next(f for f in chk["flags"] if f["entity"] == india_rem_e.SUBSIDIARY)
            check("determined within, it is flagged with counsel's name", (sub["determination"], sub["counsel"]), ("within", "Demonstration Counsel (invented)"))
            await refused("an earlier determination does not stand over a later one", ds.prepare_determination(s, firm, UserRole.COMPLIANCE, entity=india_rem_e.SUBSIDIARY, determination="outside", counsel_name="Counsel", determined_on=today - timedelta(days=30), document="Opinion, filed"), "later one stands")
            await comp.execute("record_family_determination", {**terms, "determination": "outside", "determined_on": today.isoformat()})
            chk = await ds.check(s, firm)
            check("a later determination of outside removes the flag", all(f["entity"] != india_rem_e.SUBSIDIARY for f in chk["flags"]), True)
            ov = await ds.overview(s, firm)
            check("the page lists each entity with its determination", {e["name"]: e["determination"] for e in ov["entities"]}.get(india_rem_e.SUBSIDIARY), "outside")
            check("a flag is still not a finding", "not a finding" in (await comp.execute("read_distribution_separation", {}))["message"] or "not findings" in (await comp.execute("read_distribution_separation", {}))["message"], True)

            print("\n=== B-332: a promotional text cites an approval for exactly its words ===")
            plain = "Your quarterly review is booked for Thursday. Please bring your latest statements."
            promo = "Join our evening on planning for retirement. Investments in the securities market are subject to market risks."
            check("a plain message needs no approval", (await ad_approval.gate(s, firm, plain))["required"], False)
            check("a firm outside India is never held to it", (await ad_approval.gate(s, Firm(jurisdiction="NZ", settings={}), promo, promotional=True))["required"], False)
            check("the person's flag makes a text promotional", ad_approval.classify(firm, promo, promotional=True)["promotional"], True)
            superlative = "We are the best advisers in the city and our clients love us. Act now."
            check("the check reads a superlative and pressure as promotion with no flag", (ad_approval.classify(firm, superlative)["promotional"], ad_approval.classify(firm, superlative)["labels"]), (True, ["Pressure to act", "Unqualified superlative"]))
            check("a past return is not promotion by itself: the check flags it separately", ad_approval.classify(firm, "Your portfolio returned 12% last year.")["promotional"], False)
            check("a flag cannot lower what the check found", ad_approval.classify(firm, superlative, promotional=False)["promotional"], True)
            await refused("a promotional text with no approval is refused, saying none is cited", ad_approval.gate(s, firm, promo, promotional=True), "No approval is cited")
            await refused("a made-up approval id is refused", ad_approval.gate(s, firm, promo, promotional=True, approval_id="00000000-0000-0000-0000-000000000000"), "no approval with that id")
            sub1 = await ad_approval.submit(s, firm, adviser, title="Retirement evening invitation", channel="email", text=promo)
            sid = sub1["submissions"][0]["id"]
            check("a pending submission is not an approval", sub1["submissions"][0]["approval_id"], None)
            await ad_approval.decide(s, firm, compliance, submission_id=sid, decision="approved", reason="Plain invitation with its market-risk words")
            aid = next(x for x in (await ad_approval.overview(s, firm))["submissions"] if x["id"] == sid)["approval_id"]
            ok = await ad_approval.gate(s, firm, promo, promotional=True, approval_id=aid)
            check("an approval for exactly these words stands", (ok["required"], ok["ok"], ok["approval"]["state"]), (True, True, "approved"))
            check("leading and trailing space do not matter", (await ad_approval.gate(s, firm, "  " + promo + "\n", promotional=True, approval_id=aid))["ok"], True)
            await refused("one changed word is a mismatch, naming the wording", ad_approval.gate(s, firm, promo.replace("Join", "Come to"), promotional=True, approval_id=aid), "differs from the wording")
            sub2 = await ad_approval.submit(s, firm, adviser, title="Returns promise", channel="social", text="Our portfolios give guaranteed returns of 18% a year, every year.")
            await ad_approval.decide(s, firm, compliance, submission_id=sub2["submissions"][0]["id"], decision="rejected", reason="Promises a return")
            rid = (await s.execute(select(__import__("app.models.india_batch_c", fromlist=["AdDecision"]).AdDecision).where(
                __import__("app.models.india_batch_c", fromlist=["AdDecision"]).AdDecision.decision == "rejected").order_by(__import__("app.models.india_batch_c", fromlist=["AdDecision"]).AdDecision.created_at.desc()))).scalars().first().id
            await refused("a rejected approval is refused, naming the rejection", ad_approval.gate(s, firm, "Our portfolios give guaranteed returns of 18% a year, every year.", promotional=True, approval_id=str(rid)), "rejection")
            check("the card rows name the approval standing behind the words", [r[0] for r in ad_approval.card_rows(ok)], ["Promotional", "Advertisement approval"])
            check("a plain text shows no rows", ad_approval.card_rows(await ad_approval.gate(s, firm, plain)), [])

            print("\n=== B-332: the conversation's send_client_message ===")
            hid = str(banerjee.id)
            card0 = card(await adv.preview_change("send_client_message", {"household_id": hid, "body": plain}))
            check("a plain message's card has no approval rows", ("Promotional" in card0, "Advertisement approval" in card0), (False, False))
            sent = await adv.execute("send_client_message", {"household_id": hid, "body": plain})
            check("and it sends as before", sent["messages"][0]["body"], plain)
            await refused("a promotional message with no approval is refused at its card", adv.preview_change("send_client_message", {"household_id": hid, "body": promo, "promotional": True}), "No approval is cited")
            await refused("and at the send", adv.execute("send_client_message", {"household_id": hid, "body": promo, "promotional": True}), "promotional")
            await refused("an unflagged message the check reads as promotional is refused too", adv.preview_change("send_client_message", {"household_id": hid, "body": superlative}), "promotional")
            await refused("a changed word is refused naming the mismatch", adv.preview_change("send_client_message", {"household_id": hid, "body": promo + " Bring a friend.", "promotional": True, "ad_approval_id": aid}), "differs from the wording")
            cc = card(await adv.preview_change("send_client_message", {"household_id": hid, "body": promo, "promotional": True, "ad_approval_id": aid}))
            check("the card shows the approval", ("Retirement evening invitation" in cc["Advertisement approval"], aid in cc["Advertisement approval"], "Promotional" in cc), (True, True, True))
            sent = await adv.execute("send_client_message", {"household_id": hid, "body": promo, "promotional": True, "ad_approval_id": aid})
            cite_rows = (await s.execute(select(AdCitation))).scalars().all()
            check("it sends, and what stood behind it is kept", (sent["messages"][0]["body"], len(cite_rows), cite_rows[0].item_kind, str(cite_rows[0].approval_id) == aid, cite_rows[0].text_hash == ad_approval.text_hash(promo)), (promo, 1, "message", True, True))
            check("the page lists the citation", (await ad_approval.overview(s, firm))["cited"][0]["approval_id"], aid)
            check("the tool names the flag and the approval id", all(k in {i.name for i in TOOLS["send_client_message"].inputs} for k in ("promotional", "ad_approval_id")), True)

            print("\n=== B-332: a promotional report ===")
            report = ClientReport(firm_id=firm.id, household_id=banerjee.id, title="Evening on retirement", period="As at today", status=ReportStatus.DRAFT,
                                  sections=[{"heading": "Invitation", "body": promo}], data={})
            s.add(report)
            await s.flush()
            check("a plain report passes", await reports.promotion_refusal(s, firm, report), None)
            report.data = {"promotional": True}
            check("a report marked promotional with no approval is refused for publishing", "No approval is cited" in (await reports.promotion_refusal(s, firm, report)), True)
            rsub = await ad_approval.submit(s, firm, adviser, title="Evening on retirement report", channel="website", text=reports.client_text(report))
            await ad_approval.decide(s, firm, compliance, submission_id=rsub["submissions"][0]["id"], decision="approved", reason="The report's own words, as filed")
            raid = next(x for x in (await ad_approval.overview(s, firm))["submissions"] if x["id"] == rsub["submissions"][0]["id"])["approval_id"]
            await refused("the tool refuses another approval's id", adv.preview_change("cite_report_ad_approval", {"report_id": str(report.id), "ad_approval_id": aid}), "differs from the wording")
            rc = card(await adv.preview_change("cite_report_ad_approval", {"report_id": str(report.id), "ad_approval_id": raid}))
            check("the card names the approval and says publishing is still its proposal's", ("Evening on retirement report" in rc["Advertisement approval"], "proposal" in rc["Note"]), (True, True))
            await adv.execute("cite_report_ad_approval", {"report_id": str(report.id), "ad_approval_id": raid})
            check("with the approval cited the report may be published", await reports.promotion_refusal(s, firm, report), None)
            report.sections = [{"heading": "Invitation", "body": promo + " Bring a friend."}]
            check("an edited report no longer matches its approval", "differs from the wording" in (await reports.promotion_refusal(s, firm, report)), True)
            check("a firm outside India is never held to it", await reports.promotion_refusal(s, Firm(jurisdiction="NZ", settings={}), report), None)

            print("\n=== The tools, their cards and the routes ===")
            writes = ("record_mf_figure", "record_agreement_contents", "record_family_determination", "cite_report_ad_approval")
            reads = ("read_mf_figures", "read_agreement_contents")
            check("every write pauses for confirmation and every read changes nothing", ([TOOLS[k].change_state for k in writes], [TOOLS[k].change_state for k in reads]),
                  ([ToolChangeState.PAUSE_FOR_CONFIRMATION] * 4, [ToolChangeState.NO] * 2))
            await refused("operations may record a figure but not counsel's determination", opx.preview_change("record_family_determination", terms), "compliance or a branch leader")
            from app.api import licences as api

            paths = {r.path for r in api.router.routes}
            check("each has its route", all(p in paths for p in ("/api/licences/mf-figures", "/api/licences/family-determinations", "/api/licences/agreement-contents",
                                                                 "/api/licences/agreement-contents/{household_id}")), True)
            check("the canvas has its two routes", all(p in {r.path for r in canvas.router.routes} for p in ("/api/canvas/channels", "/api/canvas/channel-requests")), True)
            from app.aurea_core import licences as licence_core

            extras = await licence_core._india_pack(s, firm)
            check("the Licences page carries the figure set, the determinations and the citations",
                  ("figure_set" in extras["mf_orders"], "determination" in extras["separation"]["entities"][0], "cited" in extras["ad_approvals"]), (True, True, True))

            print("\n=== The seed, a second time ===")
            check("the batch E seed adds nothing a second time", await india_rem_e.add(s, firm), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
