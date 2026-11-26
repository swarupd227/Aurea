"""Prove the KYC registry's status is its own state, looked up first (B-386).

A case or a client with no answer says it is not looked up and promises no timeline; an answer is recorded by a person from the
registry's response, or returned by a connected lookup (only the simulation, opt-in, tagged and discardable); on hold or rejected
carries its reasons, remediation steps and a task for the adviser, holds activation and orders, and only a newer answer clears it;
a recorded change of residential status raises a re-KYC; the book shows the status mix and the backlog by age with the contact
attempts made; documents the registry holds are not asked again; and a client reads only their own status, in plain words.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_kyc_registry
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import canvas, onboarding as api
from app.aurea_core import document_matrix, firm_time, india_onboarding, india_reminders, kyc_india, kyc_registry as k, onboarding_start, residency
from app.core.db import Base, utcnow
from app.core.tools import CLIENT_TOOLS, ONE_CLIENT_VIEWS, OPEN_VIEWS, TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Household, LegalEntity, Person, RelationshipEdge
from app.models.identity import User
from app.models.kyc import KycRecord
from app.models.kyc_registry import KycFollowUp, KycLookup
from app.models.onboarding import OnboardingParty
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
        check(label, "accepted", "refused")
    except (k.RegistryError, ExecutorError) as exc:
        said = str(exc)
        check(label, all(c.lower() in said.lower() for c in contains) or said, True)


async def count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            await run(s)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def run(s) -> None:
    firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india-demo", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                settings={"timezone": "Asia/Kolkata"})
    other = Firm(id=uuid.uuid4(), name="Astra India Two", slug="two", jurisdiction="IN", regulator="SEBI", base_currency="INR", settings={})
    us = Firm(id=uuid.uuid4(), name="Astra US", slug="us", jurisdiction="US", regulator="SEC", base_currency="USD", settings={})
    s.add_all([firm, other, us])
    await s.flush()
    today = firm_time.today(firm)
    users = {}
    for key, role, f in (("adv", UserRole.ADVISER, firm), ("ops", UserRole.OPERATIONS, firm), ("comp", UserRole.COMPLIANCE, firm),
                         ("pm", UserRole.PORTFOLIO_TEAM, firm), ("client", UserRole.CLIENT, firm), ("two", UserRole.OPERATIONS, other),
                         ("us", UserRole.OPERATIONS, us)):
        u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{key}@t", full_name=key.title(), role=role, hashed_password="x", is_active=True)
        s.add(u)
        users[key] = u
    rao = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Rao Family", values={})
    sen = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sen Family", values={})
    alien = Household(id=uuid.uuid4(), firm_id=other.id, name="Elsewhere", values={})
    s.add_all([rao, sen, alien])
    await s.flush()
    people = {}
    for name, home in (("Asha Rao", rao), ("Vikram Rao", rao), ("Meena Rao", rao), ("Dev Sen", sen), ("Not Ours", alien)):
        p = Person(id=uuid.uuid4(), firm_id=home.firm_id, household_id=home.id, full_name=name, profile={})
        s.add(p)
        people[name.split()[0]] = p
    asha, vikram, meena, dev, stranger = (people[n] for n in ("Asha", "Vikram", "Meena", "Dev", "Not"))
    users["client"].person_id = asha.id
    huf = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=rao.id, name="Rao HUF", entity_type="huf")
    s.add(huf)
    s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=users["adv"].id, to_type="household", to_id=rao.id, attributes={}))
    await s.flush()
    ex = {key: ToolExecutors(s, u.id, u.role, u.firm_id) for key, u in users.items()}
    ops = UserRole.OPERATIONS

    async def answer(who=None, status="validated", **kw):
        if isinstance(who, Person):
            kw["person_id"] = who.id
        elif who is not None:
            kw["entity_id"] = who.id
        kw.setdefault("document", "KRA status response, CVL")
        return await k.record_lookup(s, firm, ops, by="ops@t", status=status, **kw)

    async def party(name, **kw):
        return next(p for p in (await k.book(s, firm, **kw))["parties"] if p["name"] == name)

    async def tasks(contains: str) -> list[Task]:
        return [t for t in (await s.execute(select(Task).where(Task.firm_id == firm.id))).scalars().all() if contains in t.title]

    print("\n=== A case with no answer says so before any timeline ===")
    case = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Nila Iyer", registration_type="individual")
    reg = await k.case_registry(s, firm, case)
    check("the case is not looked up, and promises no timeline", (reg["status"], reg["timeline_ok"], reg["timeline"]),
          ("not_looked_up", False, "No timeline until the registry status is looked up"))
    board = await api.cases_overview(s, firm)
    check("the board carries it beside the account", board[0]["registry"]["status"], "not_looked_up")
    states = await api.states_overview(s, firm)
    row = states["accounts"][0]
    check("the queues carry it apart from the account's own state", (row["state"], row["registry"]["status"]), ("draft", "not_looked_up"))
    check("and count it", states["summary"]["registry_not_looked_up"], 1)
    said = (await ex["ops"].execute("read_onboarding_cases", {}))["message"]
    check("the conversation is told to promise nothing", ("not looked up for Nila Iyer" in said, "promise no timeline" in said), (True, True))
    check("the queues read says it too", "no KYC registry status looked up" in (await ex["ops"].execute("read_onboarding_queues", {}))["message"], True)
    check("a client with nothing recorded is not looked up, never validated", (await party("Meena Rao"))["registry"]["status"], "not_looked_up")
    check("outside India there is no registry", (await k.book(s, us), await k.case_registry(s, us, case)), ({"applies": False}, None))

    print("\n=== What an answer must say ===")
    await refused("a portfolio manager records none", k.record_lookup(s, firm, UserRole.PORTFOLIO_TEAM, by="x", person_id=asha.id, status="validated", document="KRA response"), "recorded by")
    await refused("nor a client", k.record_lookup(s, firm, UserRole.CLIENT, by="x", person_id=asha.id, status="validated", document="KRA response"), "recorded by")
    await refused("on hold needs its reason", answer(asha, "on_hold"), "carries its reason")
    await refused("validated carries no deficiency", answer(asha, "validated", deficiencies="mobile_not_verified"), "only an on-hold or rejected")
    await refused("another reason needs the registry's words", answer(asha, "on_hold", deficiencies="other"), "registry's own words")
    await refused("an unknown status", answer(asha, "cleared"), "status is one of")
    await refused("where it is filed", answer(asha, document=""), "where the answer is filed")
    await refused("not from the future", answer(asha, answered_on=(today + timedelta(days=1)).isoformat()), "future")
    await refused("no Aadhaar in a note", answer(asha, "on_hold", deficiencies="other", deficiency_note="Aadhaar 1234 5678 9012"), "never stored")
    await refused("documents held need a validated or registered answer", answer(asha, "on_hold", deficiencies="not_stated", holds="pan_card"), "leave holds out")
    await refused("another firm's person is nobody here", answer(stranger), "no such client")
    await refused("nor another firm's case", k.record_lookup(s, other, ops, by="x", case_id=case.id, status="validated", document="KRA response"), "no such onboarding case")
    await refused("nor its book", k.book(s, other, household_id=rao.id), "no such household")
    check("nothing was written by any refusal", await count(s, KycLookup), 0)

    print("\n=== The card writes nothing and names what it would do ===")
    args = {"person_id": str(asha.id), "status": "on_hold", "deficiencies": "mobile_not_verified, email_not_verified",
            "document": "KRA status response, CVL", "answered_on": (today - timedelta(days=40)).isoformat()}
    card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_kyc_lookup", args))["rows"]}
    check("the card: status, what it was, reasons, holds and the task", (card["Status"], card["Was"], card["Deficiencies"], card["Holds"], card["Task"], card["Client"]),
          ("On hold", "Not looked up", "Mobile number not verified; Email address not verified", "Activation and the household's orders", "For Adv", "Nothing is sent"))
    check("and writes nothing", (await count(s, KycLookup), await count(s, Task)), (0, 0))
    await refused("a card is refused where the write would be", ex["pm"].preview_change("record_kyc_lookup", args), "recorded by")

    print("\n=== On hold: reasons, steps, a task, and the gate ===")
    out = await ex["ops"].execute("record_kyc_lookup", args)
    check("recorded, and the message holds and sends nothing", ("on hold" in out["message"], "held until a newer answer" in out["message"], "Nothing was sent" in out["message"]), (True, True, True))
    asha_row = await party("Asha Rao")
    check("the client is on hold with both reasons", (asha_row["registry"]["status"], [d["code"] for d in asha_row["registry"]["deficiencies"]]),
          ("on_hold", ["mobile_not_verified", "email_not_verified"]))
    check("each reason has its step, not started", [(x["step"], x["state"]) for x in asha_row["steps"]],
          [("mobile_not_verified", "open"), ("email_not_verified", "open")])
    kyc_row = next(p for p in (await kyc_india.overview(s, firm))["parties"] if p["name"] == "Asha Rao")
    check("with no KYC record the answer alone blocks", (kyc_row["state"], kyc_row["recorded"]), ("blocked", False))
    check("and holds the household's orders", "Asha Rao's KYC is blocked" in (await kyc_india.household_refusal(s, firm, rao.id) or ""), True)
    task = await tasks("KYC on hold at the registry: Asha Rao")
    check("one task, for the household's adviser", [(t.assigned_to, t.household_id) for t in task], [(users["adv"].id, rao.id)])
    await refused("the same answer twice changes nothing", answer(asha, "on_hold", deficiencies="mobile_not_verified, email_not_verified",
                  answered_on=args["answered_on"], document="KRA status response, CVL"), "nothing changes")
    await answer(asha, "on_hold", deficiencies="mobile_not_verified,email_not_verified", reference="R-2", answered_on=args["answered_on"])
    check("a re-check still on hold raises no second task", len(await tasks("Asha Rao")), 1)
    plan = await india_reminders.plan(s, firm)
    check("the reminders raise no second one either", [i["item"]["label"] for i in plan["items"] if "Asha Rao" in i["item"]["label"]], [])

    lookup_id = (await party("Asha Rao"))["registry"]["lookup_id"]
    first = (await s.execute(select(KycLookup).where(KycLookup.firm_id == firm.id).order_by(KycLookup.created_at))).scalars().first()
    await refused("a step of an answer no longer standing", k.record_step(s, firm, ops, by="x", lookup_id=first.id, step="mobile_not_verified", state="asked"), "newer registry answer")
    await refused("a step the answer does not ask", k.record_step(s, firm, ops, by="x", lookup_id=lookup_id, step="address_proof", state="asked"), "step of that answer")
    await refused("not needed says why", k.record_step(s, firm, ops, by="x", lookup_id=lookup_id, step="mobile_not_verified", state="not_needed"), "why")
    await refused("another firm's answer", k.record_step(s, other, ops, by="x", lookup_id=lookup_id, step="mobile_not_verified", state="asked"), "no such registry answer")
    for step in ("mobile_not_verified", "email_not_verified"):
        await ex["ops"].execute("record_kyc_step", {"lookup_id": lookup_id, "step": step, "state": "done"})
    asha_row = await party("Asha Rao")
    check("every step done clears nothing", ([x["state"] for x in asha_row["steps"]], asha_row["registry"]["status"]), (["done", "done"], "on_hold"))
    check("and the book still holds", (await kyc_india.household_refusal(s, firm, rao.id)) is not None, True)

    print("\n=== A KYC record that only carries the old status states nothing ===")
    await kyc_india.record(s, firm, by="adv@t", document="KRA acknowledgement", person_id=asha.id, kra_status="validated", risk_band="low",
                           kyc_completed_on=(today - timedelta(days=400)).isoformat(), verification_mode="video_kyc", pan_aadhaar_linked=True,
                           ckyc_number="12345678901234")
    check("a status given on a record is a newer statement", (await party("Asha Rao"))["registry"]["status"], "validated")
    await answer(asha, "on_hold", deficiencies="pan_aadhaar_not_linked", reference="R-3")
    await kyc_india.record(s, firm, by="adv@t", document="Risk review", person_id=asha.id, risk_band="medium")
    check("a later record that carries it over does not clear a hold", (await party("Asha Rao"))["registry"]["status"], "on_hold")
    kyc_row = next(p for p in (await kyc_india.overview(s, firm))["parties"] if p["name"] == "Asha Rao")
    check("the KYC read shows the standing answer", (kyc_row["kra_status"], kyc_row["state"]), ("on_hold", "blocked"))
    await kyc_india.record(s, firm, by="adv@t", document="KRA acknowledgement after remediation", person_id=asha.id, kra_status="validated")
    check("a person saying validated again clears it", ((await party("Asha Rao"))["registry"]["status"], await kyc_india.household_refusal(s, firm, rao.id)), ("validated", None))
    check("and there is nothing to put right", (await party("Asha Rao"))["steps"], [])

    print("\n=== The book: the mix, the backlog by age, the attempts ===")
    await answer(vikram, "registered", answered_on=(today - timedelta(days=100)).isoformat())
    await answer(dev, "rejected", deficiencies="identity_proof", answered_on=(today - timedelta(days=200)).isoformat())
    await answer(huf, "validated", holds="pan_card,identity_proof")
    book = await k.book(s, firm)
    check("the mix", {m["status"]: m["count"] for m in book["mix"] if m["count"]},
          {"validated": 2, "registered": 1, "rejected": 1, "not_looked_up": 1})
    check("the backlog is oldest first", [(p["name"], p["backlog"]["band"]) for p in book["backlog"]], [("Dev Sen", "over_180"), ("Vikram Rao", "91_180")])
    check("none has a contact attempt", (book["counts"]["no_contact"], [b["no_contact"] for b in book["bands"]]), (2, [0, 0, 1, 1]))
    check("a rejected answer asks for a fresh KYC too", [x["step"] for x in (await party("Dev Sen"))["steps"]], ["identity_proof", "fresh_kyc"])
    check("a household with no adviser: the task is nobody's", [t.assigned_to for t in await tasks("Dev Sen")], [None])
    await refused("an outcome the list has", k.record_contact(s, firm, ops, by="x", person_id=dev.id, channel="phone", outcome="ignored"), "outcome is one of")
    await ex["adv"].execute("record_kyc_contact", {"person_id": str(dev.id), "channel": "phone", "outcome": "no_answer", "happened_on": (today - timedelta(days=3)).isoformat()})
    await k.record_contact(s, firm, ops, by="ops@t", person_id=vikram.id, channel="email", outcome="replied", happened_on=(today - timedelta(days=150)).isoformat())
    book = await k.book(s, firm)
    dev_row = await party("Dev Sen")
    check("the attempt shows with its day", (dev_row["contacts"]["attempts"], dev_row["contacts"]["last_outcome"]), (1, "No answer"))
    check("an attempt before the item opened does not count for it", (await party("Vikram Rao"))["contacts"]["attempts"], 0)
    check("so one aged item has no attempt", book["counts"]["no_contact"], 1)
    check("one household's book is that household's", sorted(p["name"] for p in (await k.book(s, firm, household_id=sen.id))["parties"]), ["Dev Sen"])
    check("another firm reads none of it", ((await k.book(s, other))["counts"]["backlog"], [p["name"] for p in (await k.book(s, other))["parties"]]), (0, ["Not Ours"]))
    said = (await ex["comp"].execute("read_kyc_registry", {}))["message"]
    check("the read says the mix, the backlog and what is not connected", ("1 rejected" in said, "no contact attempt" in said, "No KRA or CKYC lookup is connected" in said), (True, True, True))

    print("\n=== A case's answers ===")
    await refused("the case needs the registry, not a guess", k.record_lookup(s, firm, ops, by="x", status="validated", document="KRA response"), "whose it is")
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=case.id, status="on_hold", deficiencies="pan_aadhaar_not_linked", document="KRA response for the case")
    reg = await k.case_registry(s, firm, case)
    check("the case is on hold, its timeline waits", (reg["status"], reg["timeline"]), ("on_hold", "No timeline until the registry hold is cleared"))
    gate = next(g for g in (await api._gates_for(s, firm, case))["gates"] if g["key"] == "kyc_current")
    check("activation is refused", gate["state"], "fail")
    check("the case's task is for the adviser who opened it", [t.assigned_to for t in await tasks("Nila Iyer")], [users["adv"].id])
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=case.id, status="validated", document="KRA response after linking", holds="identity_proof,pan_card")
    reg = await k.case_registry(s, firm, case)
    check("a newer answer clears it", (reg["status"], reg["timeline_ok"]), ("validated", True))
    gate = next(g for g in (await api._gates_for(s, firm, case))["gates"] if g["key"] == "kyc_current")
    check("and the gate no longer fails (no KYC record still warns)", gate["state"], "warn")
    joint = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Om Bose", registration_type="joint_anyone")
    s.add_all([OnboardingParty(firm_id=firm.id, case_id=joint.id, role="owner", legal_name="Om Bose"),
               OnboardingParty(firm_id=firm.id, case_id=joint.id, role="joint_owner", legal_name="Ira Bose")])
    await s.flush()
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=joint.id, party_name="Om Bose", status="validated", document="KRA response", holds="identity_proof")
    reg = await k.case_registry(s, firm, joint)
    check("one holder not looked up: the case is not looked up", (reg["status"], [(p["name"], p["status"]) for p in reg["parties"]]),
          ("not_looked_up", [("Ira Bose", "not_looked_up"), ("Om Bose", "validated")]))

    print("\n=== What the registry holds is not asked again ===")

    async def asked(c) -> list[str]:
        return [x["key"] for x in (await document_matrix.check_case(s, c, firm))["requirements"]["documents"] if not x["on_file"]]

    check("one of two joint holders held: identity is still asked", await asked(joint), ["id"])
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=joint.id, party_name="Ira Bose", status="validated", document="KRA response", holds="identity_proof")
    check("both held: it is not", await asked(joint), [])
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=joint.id, party_name="Ira Bose", status="on_hold", deficiencies="not_stated", document="KRA response")
    check("an on-hold holder holds nothing", await asked(joint), ["id"])
    nri = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Arun Das", registration_type="nri_nre")
    before = await asked(nri)
    check("an NRI account asks identity and PAN", [x for x in ("id", "pan") if x in before], ["id", "pan"])
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=nri.id, status="validated", document="KRA response")
    check("a validated answer listing nothing holds neither for an NRI", [x for x in ("id", "pan") if x in await asked(nri)], ["id", "pan"])
    await k.record_lookup(s, firm, ops, by="ops@t", case_id=nri.id, status="validated", document="KRA response", reference="again", holds="identity_proof,pan_card")
    after = await asked(nri)
    check("listing them, neither is asked", [x for x in ("id", "pan") if x in after], [])
    check("the rest of the NRI papers still are", sorted(after), sorted(x for x in before if x not in ("id", "pan")))

    print("\n=== A change of residential status raises a re-KYC ===")

    async def residence(**facts):
        return await residency.record(s, firm, by="adv@t", person_id=meena.id, document="Passport and OCI card", **facts)

    await kyc_india.record(s, firm, by="adv@t", document="KRA acknowledgement", person_id=meena.id, kra_status="validated", risk_band="low",
                           kyc_completed_on=(today - timedelta(days=300)).isoformat(), verification_mode="video_kyc", pan_aadhaar_linked=True,
                           ckyc_number="12345678901234")
    first = await residence(fema_status="resident", country_of_residence="India")
    check("the first record is no change: what it was before is not known", (first["re_kyc"], await tasks("Re-KYC: Meena Rao")), (None, []))
    card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_residency", {"person_id": str(meena.id), "document": "OCI card", "fema_status": "nri"}))["rows"]}
    check("the card says it raises a re-KYC and for whom", ("refresh" in card.get("Re-KYC", ""), "Adv" in card.get("Re-KYC", "")), (True, True))
    check("and writes nothing", await tasks("Re-KYC: Meena Rao"), [])
    out = await ex["adv"].execute("record_residency", {"person_id": str(meena.id), "document": "OCI card", "fema_status": "nri"})
    check("resident to NRI raises it", ("raises a re-KYC" in out["message"], [t.assigned_to for t in await tasks("Re-KYC: Meena Rao")]), (True, [users["adv"].id]))
    row = next(p for p in (await kyc_india.overview(s, firm))["parties"] if p["name"] == "Meena Rao")
    check("the KYC is due, naming the change", (row["state"], "Change of residential status" in row["message"], row["kra_status"]), ("due", True, "validated"))
    check("it is in the backlog", "Change of residential status" in (await party("Meena Rao"))["backlog"]["reasons"], True)
    again = await residence(accounts="NRO")
    check("a record that changes no status raises nothing", again["re_kyc"], None)
    moved = await residence(country_of_residence="Singapore")
    check("a new country of residence does", (bool(moved["re_kyc"]), len(await tasks("Re-KYC: Meena Rao"))), (True, 1))

    print("\n=== The client reads their own, in plain words ===")
    await answer(asha, "on_hold", deficiencies="other,mobile_not_verified", deficiency_note="PMLA desk: internal concern", reference="R-9")
    await kyc_india.record(s, firm, by="comp@t", document="Screening note", person_id=asha.id, pep="yes", trigger="adverse_media", note="watch")
    mine = await ex["client"].execute("read_my_kyc_status", {})
    asha_mine = next(p for p in mine["people"] if p["name"] == "Asha Rao")
    check("their own household only", sorted(p["name"] for p in mine["people"]), ["Asha Rao", "Meena Rao", "Rao HUF", "Vikram Rao"])
    check("on hold, with the plain reason and what to do", (asha_mine["label"], asha_mine["reasons"], [x["action"] for x in asha_mine["steps"]]),
          ("On hold", ["Mobile number not verified"], ["Ask your adviser what the KYC agency needs", "Verify your mobile number with the KYC agency"]))
    meena_mine = next(p for p in mine["people"] if p["name"] == "Meena Rao")
    check("the residency change is said", meena_mine["refresh"], "Your residential status changed: your KYC needs refreshing")
    text = json.dumps(mine).lower()
    check("nothing about screening, monitoring, risk or who recorded it",
          [w for w in ("pmla", "internal", "screening", "adverse", "pep", "politic", "risk", "suspicious", "monitor", "ops@t", "comp@t", "dev sen", "document\"") if w in text], [])
    page = await canvas.canvas_kyc(household_id=sen.id, user=users["client"], firm=firm, db=s)
    check("the client's page ignores another household asked for", (page["household_id"], "Dev Sen" in json.dumps(page)), (str(rao.id), False))
    check("the client holds the read and none of the writes",
          [n for n in ("read_my_kyc_status", "read_kyc_registry", "record_kyc_lookup", "lookup_kyc_registry", "record_kyc_step", "record_kyc_contact") if n in CLIENT_TOOLS],
          ["read_my_kyc_status"])
    check("it takes no household id", TOOLS["read_my_kyc_status"].inputs, [])

    print("\n=== The tools and the view ===")
    writes = ("record_kyc_lookup", "lookup_kyc_registry", "record_kyc_step", "record_kyc_contact")
    check("every write is behind a Confirm card", [TOOLS[n].change_state for n in writes], [ToolChangeState.PAUSE_FOR_CONFIRMATION] * 4)
    check("the read changes nothing", TOOLS["read_kyc_registry"].change_state, ToolChangeState.NO)
    check("no research, portfolio or client role writes", [r for n in writes for r in TOOLS[n].roles_required if r in (UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.CLIENT)], [])
    check("it opens beside the conversation, on one household too", ("kyc_registry" in OPEN_VIEWS, "kyc_registry" in ONE_CLIENT_VIEWS), (True, True))

    print("\n=== A lookup only where one is connected: the simulation, tagged and discardable ===")
    from app import simulations
    from app.simulations import kyc_registry as sim

    k.PROVIDERS.clear()
    await refused("none connected: it says so and looks nothing up", ex["ops"].execute("lookup_kyc_registry", {"person_id": str(vikram.id)}), "no kra or ckyc lookup is connected")
    sim.install()
    await refused("installed is not opted in", ex["ops"].execute("lookup_kyc_registry", {"person_id": str(vikram.id)}), "no kra or ckyc lookup is connected")
    before_rows = (await count(s, KycLookup), await count(s, Task), await count(s, KycFollowUp))
    await sim.seed(s, firm)
    check("seeding only opts the firm in", (simulations.enabled_for(firm, "kyc_registry"), await count(s, KycLookup)), (True, before_rows[0]))
    card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("lookup_kyc_registry", {"person_id": str(vikram.id)}))["rows"]}
    check("the card says it is simulated and invented", card["Simulated"], "Yes: an invented answer, not a KRA check")
    check("and asks the provider nothing", await count(s, KycLookup), before_rows[0])
    got = await ex["ops"].execute("lookup_kyc_registry", {"person_id": str(vikram.id)})
    vik = await party("Vikram Rao")
    check("the answer repeats what stands and is chipped", (vik["registry"]["status"], vik["registry"]["simulated"], got["message"].startswith("Simulated lookup")),
          ("registered", True, True))
    scripted = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Nandini Kapoor", registration_type="individual")
    await ex["ops"].execute("lookup_kyc_registry", {"case_id": str(scripted.id)})
    reg = await k.case_registry(s, firm, scripted)
    check("a scripted name comes back on hold with its reasons", (reg["status"], reg["simulated"], len(reg["parties"][0]["steps"])), ("on_hold", True, 2))
    rows = (await s.execute(select(KycLookup).where(KycLookup.source_system.is_not(None)))).scalars().all()
    check("every row it answered is tagged", {r.source_system for r in rows}, {"simulated:kyc_registry"})
    check("and so is the task it raised", [t.source for t in await tasks("Nandini Kapoor")], ["simulated:kyc_registry"])
    await k.record_step(s, firm, ops, by="ops@t", lookup_id=reg["parties"][0]["lookup_id"], step="mobile_not_verified", state="asked")
    await sim.discard(s, firm)
    check("discarding removes the answers, their steps and tasks, and the opt-in",
          (await count(s, KycLookup), await count(s, Task), await count(s, KycFollowUp), simulations.enabled_for(firm, "kyc_registry")), (*before_rows, False))
    check("and the case is not looked up again", (await k.case_registry(s, firm, scripted))["status"], "not_looked_up")
    k.PROVIDERS.clear()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
