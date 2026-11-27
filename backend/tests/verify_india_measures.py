"""Prove India's onboarding measures and the client's view (B-392).

Each measure is counted from what is on file, with its definition and count, and reads "not assessed" where nothing is recorded:
cycle time by cohort, the straight-through rate and its counted touches, the registry mix and re-KYC backlog, first-attempt
verification by cause, mandates and first debits, rejects by cause, nominations, screening disposition time and the delta rescreen
backlog, and report timeliness in compliance's reading alone, both read from what the PMLA programme records (B-390), with no second
report or investigation seeded for a household. The Indian RACI replaces the American one for an Indian firm only. A client reads the step each of their own
accounts has reached and nothing about screening or monitoring. A minor's conversion task is raised a year before majority.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_measures
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import canvas, onboarding_scoreboard as api
from app.aurea_core import (client_onboarding, firm_time, fiu_reports, india_deadlines, india_reminders, india_scoreboard as m, law_figures,
                            txn_monitoring)
from app.core.db import Base, utcnow
from app.core.tools import CLIENT_TOOLS, TOOLS, ToolChangeState
from app.llm.executors import ToolExecutors
from app.models.aml_monitoring import AmlInvestigation
from app.models.aml_review import ScreeningDecision
from app.models.consolidation import ConsolidationItem, ConsolidationStep
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.identity_checks import VerificationAttempt
from app.models.india_batch_a import DebitMandate
from app.models.india_money import MandateDebit
from app.models.kyc_registry import KycLookup
from app.models.onboarding import OnboardingCase, OnboardingParty, TransferRequest
from app.models.pmla import PepApproval, ScreeningListUpdate
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def by_key(view: dict) -> dict:
    return {x["key"]: x for x in view["metrics"]}


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
    seeded = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with seeded.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(seeded, expire_on_commit=False)() as s:
            await demonstration(s)
    finally:
        await seeded.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def run(s) -> None:
    firm = Firm(id=uuid.uuid4(), name="Astra India", slug="in", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                settings={"timezone": "Asia/Kolkata", "licences": ["investment_adviser"]})
    other = Firm(id=uuid.uuid4(), name="Other India", slug="in2", jurisdiction="IN", regulator="SEBI", base_currency="INR", settings={})
    us = Firm(id=uuid.uuid4(), name="Astra US", slug="us", jurisdiction="US", regulator="SEC", base_currency="USD", settings={})
    s.add_all([firm, other, us])
    await s.flush()
    sharma, rao = Household(id=uuid.uuid4(), firm_id=firm.id, name="Sharma"), Household(id=uuid.uuid4(), firm_id=firm.id, name="Rao")
    s.add_all([sharma, rao])
    await s.flush()
    today = firm_time.today(firm)
    asha = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=sharma.id, full_name="Asha Sharma", date_of_birth=date(1980, 1, 1))
    dev = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=rao.id, full_name="Dev Rao", date_of_birth=date(1975, 1, 1))
    s.add_all([asha, dev])
    await s.flush()
    users = {}
    for key, role in (("adv", UserRole.ADVISER), ("ops", UserRole.OPERATIONS), ("comp", UserRole.COMPLIANCE), ("para", UserRole.PARAPLANNER),
                      ("branch", UserRole.BRANCH_LEADER), ("client", UserRole.CLIENT)):
        users[key] = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key.title(), role=role, hashed_password="x",
                          person_id=asha.id if key == "client" else None)
    us_adv = User(id=uuid.uuid4(), firm_id=us.id, email="usadv@t", full_name="US Adv", role=UserRole.ADVISER, hashed_password="x")
    s.add_all([*users.values(), us_adv])
    s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=users["adv"].id, to_type="household", to_id=sharma.id))
    await s.flush()
    ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}

    print("\n=== Nothing on file: not assessed, never zero ===")
    empty = await m.measures(s, firm, UserRole.COMPLIANCE)
    check("ten measures for compliance, in the module's order", [x["key"] for x in empty["metrics"]],
          ["cycle_time", "straight_through", "kyc_mix", "verification", "mandates", "rejects", "nomination", "screening", "str_timeliness", "rekyc_backlog"])
    check("none is assessed and each says so", ({x["assessed"] for x in empty["metrics"]}, {x["count"] for x in empty["metrics"]}), ({False}, {"not assessed"}))
    check("each carries its definition", [x["key"] for x in empty["metrics"] if len(x["definition"]) < 30], [])
    check("no rate is a zero", [by_key(empty)["straight_through"]["data"]["all"]["rate_pct"], by_key(empty)["nomination"]["data"]["rate_pct"],
                                by_key(empty)["screening"]["data"]["within_pct"], by_key(empty)["str_timeliness"]["data"]["in_time_pct"]], [None] * 4)
    for role in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN, None):
        check(f"report timeliness is left out for {role}", "str_timeliness" in by_key(await m.measures(s, firm, role)), False)

    now = utcnow()
    ago = lambda days: now - timedelta(days=days)  # noqa: E731

    def case(name, reg, start, opened=None, *, household=sharma, entity=False, status=None, firm_id=firm.id, **more):
        row = OnboardingCase(id=uuid.uuid4(), firm_id=firm_id, prospect_name=name, registration_type=reg, is_entity=entity, created_at=ago(start),
                             status=status or ("approved" if opened is not None else "intake"), intake={"household_id": str(household.id)} if household else {},
                             custodian_push_status="active" if opened is not None else None, custodian_push_at=ago(opened) if opened is not None else None,
                             activated_at=ago(opened) if opened is not None else None, **more)
        s.add(row)
        return row

    a = case("Asha Sharma", "individual", 10, 9)
    b = case("Bala Rao", "individual", 20, 15, household=rao, nigo_root_cause="missing_signature")
    c = case("Chitra Rao", "nri_nre", 30, 18, household=rao)
    d = case("Rao HUF", "huf", 40, 20, household=rao, entity=True)
    e = case("Meera Sharma", "joint_anyone", 12, None, agreement_status="sent", agreement_sent_at=ago(3), nigo_flag=True,
             nigo_reason="PMLA desk: screening query on the payer", nigo_root_cause="title_mismatch")
    f = case("Refused Sharma", "individual", 12, None, status="rejected")
    g = case("Gopal Rao", "individual", 12, None, household=rao, status="approved")
    h = case("Hari Sharma", "individual", 9, None, status="review", agreement_status="signed", agreement_sent_at=ago(8), agreement_signed_at=ago(7),
             edd_status="edd_pending")
    case("Other Firm", "individual", 10, 1, household=None, firm_id=other.id)
    await s.flush()
    s.add(PepApproval(firm_id=firm.id, case_id=b.id, parties=["Bala Rao"], basis=[], decision="approve", approver="Head of compliance", reason="Reviewed",
                      evidence="Minute", decided_on=today - timedelta(days=16), recorded_by="comp@t"))
    s.add(TransferRequest(firm_id=firm.id, case_id=c.id, transfer_type="demat", direction="in", status="failed", reject_reason="Signature mismatch",
                          initiated_at=ago(17)))
    s.add(TransferRequest(firm_id=firm.id, case_id=c.id, transfer_type="demat", direction="in", status="settled", initiated_at=ago(17), settled_at=ago(16)))

    def attempt(party, kind, method, outcome, at, *, case_id=None, person=None, reason=None, last4=None):
        s.add(VerificationAttempt(firm_id=firm.id, person_id=person.id if person else None, case_id=case_id, party_name=party, kind=kind, method=method,
                                  outcome=outcome, reason=reason, last4=last4, recorded_by="ops@t", created_at=ago(at)))

    attempt("Asha Sharma", "pan", "provider", "failed", 9, person=asha, reason="pan_invalid")
    attempt("Asha Sharma", "pan", "alternative", "passed", 8, person=asha)
    attempt("Dev Rao", "bank", "alternative", "passed", 8, person=dev, last4="1111")
    attempt("Chitra Rao", "vipv", "alternative", "passed", 25, case_id=c.id)
    s.add(KycLookup(firm_id=firm.id, case_id=d.id, party_name="Rao HUF", registry="kra", status="on_hold", answered_on=today - timedelta(days=30),
                    deficiencies=["identity_proof"], holds=[], document="KRA response", recorded_by="ops@t", created_at=ago(30)))
    s.add(KycLookup(firm_id=firm.id, person_id=asha.id, party_name="Asha Sharma", registry="kra", status="validated", answered_on=today,
                    deficiencies=[], holds=[], document="KRA response", recorded_by="ops@t", created_at=ago(1)))

    # Mandates: two registered (one first debit confirmed, one failed then confirmed), one failed registration, one pending.
    mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=asha.id, name="Asha - Core", mandate_type="advisory")
    held = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=dev.id, name="Dev - Core", mandate_type="advisory")
    huf = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=rao.id, name="Rao HUF", entity_type="huf")
    s.add_all([mandate, held, huf])
    await s.flush()
    family = Mandate(id=uuid.uuid4(), firm_id=firm.id, entity_id=huf.id, name="HUF - Core", mandate_type="advisory")
    s.add(family)
    await s.flush()
    acc = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Asha demat", custodian="CDSL", currency="INR")
    acc2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=held.id, name="Dev demat", custodian="CDSL", currency="INR")
    acc3 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=family.id, name="HUF demat", custodian="CDSL", currency="INR")
    s.add_all([acc, acc2, acc3])
    await s.flush()
    s.add(AccountBeneficiary(firm_id=firm.id, account_id=acc.id, beneficiary_name="Kiran", percentage=100, designation_class="primary"))

    def debit_mandate(ref, kind, status="active"):
        s.add(DebitMandate(firm_id=firm.id, account_id=acc.id, reference=ref, mandate_type="upi_autopay" if ref == "P" else "nach", kind=kind,
                           per_debit_limit=10000, starts_on=today - timedelta(days=40), status=status, source_document="Confirmation", recorded_by="ops@t",
                           created_at=ago(40)))

    for ref, kind in (("R1", "registered"), ("R2", "registered"), ("F1", "failed"), ("P", "requested")):
        debit_mandate(ref, kind)
    for ref, outcome, at in (("R1", "confirmed", 20), ("R2", "failed", 20), ("R2", "confirmed", 10)):
        s.add(MandateDebit(firm_id=firm.id, account_id=acc.id, reference=ref, outcome=outcome, debited_on=today - timedelta(days=at), amount=5000,
                           source_document="Notice", recorded_by="ops@t", created_at=ago(at)))

    inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="X", name="X", asset_class="equity", market_type="public", currency="INR")
    s.add(inst)
    await s.flush()
    for status, why in (("filled", None), ("rejected", "Bank account mismatch at the platform"), ("draft", None)):
        s.add(Order(firm_id=firm.id, account_id=acc.id, instrument_id=inst.id, mandate_id=mandate.id, side="buy", status=status, quantity=1,
                    est_price=1, est_value=1, rejected_reason=why, reason="test", venue="test"))
    item = ConsolidationItem(id=uuid.uuid4(), firm_id=firm.id, household_id=sharma.id, kind="code_change", institution="A fund house", holding_keys=[],
                             lines=[], details={}, opened_by="ops@t", opened_on=today)
    s.add(item)
    await s.flush()
    for seq, step, note in ((1, "submitted", None), (2, "rejected", "Signature does not match the registrar's")):
        s.add(ConsolidationStep(firm_id=firm.id, item_id=item.id, seq=seq, step=step, happened_on=today, evidence="Letter", note=note, recorded_by="ops@t"))

    # Screening: one open past the line, one decided in a day, one in ten, one timed from the log after a rescreen, one that cannot be timed.
    async def screened(case_row, name, at, decided=None, log_at=None):
        hit = {"category": "pep", "matched_name": f"{name} listed", "score": 0.7}
        party = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=case_row.id, role="owner", legal_name=name, screening_status="review",
                                screening_hits=[hit], screened_at=ago(at))
        s.add(party)
        if log_at is not None:
            case_row.screening_log = [{"party": name, "screened_at": ago(log_at).isoformat()}]
        await s.flush()
        if decided is not None:
            s.add(ScreeningDecision(firm_id=firm.id, case_id=case_row.id, party_id=party.id, hit_key=f"pep:{name} listed", category="pep",
                                    kind="disposition", decision="false_positive", rationale="Different date of birth", decided_by="ops@t",
                                    decided_by_role="operations", created_at=ago(decided)))

    await screened(e, "Meera Sharma", 8)
    await screened(h, "Hari Sharma", 10, decided=9)
    await screened(c, "Chitra Rao", 30, decided=20)
    await screened(d, "Rao HUF", 1, decided=25, log_at=27)
    await screened(b, "Bala Rao", 1, decided=18)
    asked = (await s.execute(select(OnboardingParty).where(OnboardingParty.firm_id == firm.id, OnboardingParty.legal_name == "Meera Sharma"))).scalar_one()
    s.add(ScreeningDecision(firm_id=firm.id, case_id=e.id, party_id=asked.id, hit_key="pep:Meera Sharma listed", category="pep", kind="disposition",
                            decision="needs_info", rationale="Asked for the date of birth", decided_by="ops@t", decided_by_role="operations",
                            created_at=ago(7)))      # asking for more leaves the hit open

    def investigation(suspicion, reported=None, status="open", formed=True):
        day = today - timedelta(days=suspicion) if suspicion is not None else None
        s.add(AmlInvestigation(firm_id=firm.id, household_id=rao.id, detected_on=today - timedelta(days=60), status=status, opened_by="comp@t",
                               suspicion_on=day, reported_on=today - timedelta(days=reported) if reported is not None else None,
                               steps=[{"step": "suspicion_formed", "on": day.isoformat()}] if day is not None and formed else []))

    investigation(40, 36, "reported")
    investigation(40, 10, "reported")
    investigation(30)
    investigation(1)
    investigation(30, None, "closed")
    investigation(None)
    investigation(20, 18, "reported", formed=False)     # filed with no day of suspicion on the record
    await s.flush()

    print("\n=== Counted from the record ===")
    got = by_key(await m.measures(s, firm, UserRole.COMPLIANCE))
    cy = got["cycle_time"]["data"]
    check("cycle time by cohort: count, P50, P90", [(cy[k]["n"], cy[k]["p50"], cy[k]["p90"]) for k in ("resident", "nri", "entity")],
          [(2, 1.0, 5.0), (1, 12.0, 12.0), (1, 20.0, 20.0)])
    check("cases with no opening are named, the rejected one is not counted", cy["not_opened"], 3)
    st = got["straight_through"]["data"]
    check("one of four opened with no touch", (st["all"]["opened"], st["all"]["straight_through"], st["all"]["rate_pct"]), (4, 1, 25.0))
    check("residents: one of two", (st["resident"]["straight_through"], st["resident"]["opened"]), (1, 2))
    kinds = {k: v for k, v in st["by_kind"].items() if v}
    check("each touch is counted by its kind", kinds, {"nigo": 1, "registry_by_hand": 1, "check_by_hand": 1, "screening_decision": 3, "pep_approval": 1, "failed_transfer": 1})
    mix = got["kyc_mix"]["data"]["mix"]
    check("the registry mix comes from the book", (mix.get("validated"), mix.get("not_looked_up", 0) >= 1, got["kyc_mix"]["assessed"]), (1, True, True))
    v = got["verification"]["data"]
    check("PAN: first attempt failed, passed by hand after", (v["pan"]["first_attempts"], v["pan"]["first_passed"], v["pan"]["passed_by_hand"], v["pan"]["rate_pct"]),
          (1, 0, 1, 0.0))
    check("penny drop and video passed first time, by hand", [(v[k]["first_passed"], v[k]["first_by_hand"]) for k in ("bank", "vipv")], [(1, 1), (1, 1)])
    check("Aadhaar eKYC is not assessed: nothing records it", (v["ekyc"]["first_attempts"], got["verification"]["rows"][0][1]), (0, "not assessed"))
    check("the failure has its cause", v["causes"], [{"check": "PAN verification", "cause": "PAN not found", "count": 1}])
    md = got["mandates"]["data"]
    check("registrations: two succeeded, one failed, one pending", (md["all"]["registered"], md["all"]["failed"], md["all"]["pending"], md["all"]["registration_pct"]),
          (2, 1, 1, 66.7))
    check("first debit is the first presented: one of two", (md["all"]["first_confirmed"], md["all"]["first_failed"], md["all"]["first_debit_pct"]), (1, 1, 50.0))
    check("a rail with nothing recorded is not assessed", got["mandates"]["rows"][2][1], "not assessed")
    rj = got["rejects"]["data"]
    check("rejects by source", {k: (rj[k]["rejected"], rj[k]["submitted"]) for k in ("orders", "registrar", "opening", "transfers", "returns")},
          {"orders": (1, 2), "registrar": (1, 1), "opening": (0, 4), "transfers": (1, 2), "returns": (0, 0)})
    check("by the cause recorded", (rj["orders"]["causes"], list(rj["registrar"]["causes"])),
          ({"Bank account mismatch at the platform": 1}, ["Signature does not match the registrar's"]))
    check("a source with nothing submitted is not assessed", got["rejects"]["rows"][-1][1:], ["not assessed"] * 3)
    nm = got["nomination"]["data"]
    check("nominations: one of two individual accounts, the HUF's not checked", (nm["covered"], nm["checked"], nm["not_checked"], nm["rate_pct"]), (1, 2, 1, 50.0))
    sc = got["screening"]["data"]
    check("with no list change recorded the rescreen backlog is not assessed",
          (sc["list_changes_recorded"], sc["rescreen_backlog"], got["screening"]["rows"][-2][1], got["screening"]["rows"][-1][1]),
          (False, None, "not assessed", "not assessed"))
    s.add(ScreeningListUpdate(firm_id=firm.id, list_key="uapa", version="T-1", published_on=today - timedelta(days=3), entries=[], removed=[],
                              source_document="Notification", demonstration=True, recorded_by="comp@t", created_at=ago(3)))
    await s.flush()
    listed = by_key(await m.measures(s, firm, UserRole.COMPLIANCE))["screening"]
    check("after a change, the parties not screened since are the backlog, aged from it",
          (listed["data"]["rescreen_backlog"], listed["data"]["rescreen_oldest_days"], listed["rows"][-1][1],
           "Includes invented demonstration list entries" in listed["chips"]), (3, 3, "3 d", True))
    check("screening: five alerts, one open past the five-day line", (sc["alerts"], sc["open"], sc["open_over_sla"], sc["sla_days"]), (5, 1, 1, 5))
    check("three timed (one from the log), two within, one with no earlier time", (sc["timed"], sc["within_sla"], sc["no_start_on_file"]), (3, 2, 1))
    check("false positives of what was decided", sc["false_positive_pct"], 100.0)
    rep = got["str_timeliness"]["data"]
    check("reports: in time, late, overdue, within time, closed without one, filed with no day", [rep[k] for k in
          ("in_time", "late", "overdue", "open", "no_report", "not_measured")], [1, 1, 1, 1, 1, 1])
    check("one of three judged was in time", (rep["measured"], rep["in_time_pct"]), (3, 33.3))
    pmla = await fiu_reports.overview(s, firm)
    check("the figures are the monitoring page's own", (rep["in_time"], rep["late"], rep["overdue"], rep["monthly"]),
          (pmla["str"]["in_time"], pmla["str"]["late"], pmla["str"]["overdue"], pmla["counts"]))
    check("the line is the PMLA programme's law row, once, marked assumed",
          (m.STR_FIGURE, "in_str_due_working_days" in law_figures.VERSIONS, law_figures.get(m.STR_FIGURE).source.startswith("ASSUMED")),
          ("in_pml_str_working_days", False, True))
    check("and agrees with the report clock", int(law_figures.value(m.STR_FIGURE, today)), txn_monitoring.REGIMES["IN"]["working_days"])
    rk = got["rekyc_backlog"]["data"]
    check("the re-KYC backlog by age, with no contact", (rk["open"] >= 1, rk["no_contact"] >= 1, got["rekyc_backlog"]["assessed"]), (True, True, True))
    check("another firm's records are not counted", by_key(await m.measures(s, other, UserRole.COMPLIANCE))["cycle_time"]["data"]["resident"]["n"], 1)

    print("\n=== The scoreboard, its roles and the Indian RACI ===")
    for key in ("adv", "ops", "para", "branch"):
        out = await ex[key].execute("read_onboarding_scoreboard", {})
        check(f"{key} reads the measures without report timeliness", ("str_timeliness" in by_key(out["india"]), "suspicious" in json.dumps(out["india"]).lower(),
                                                                    "suspicious" in out["message"].lower()), (False, False, False))
    comp = await ex["comp"].execute("read_onboarding_scoreboard", {})
    check("compliance reads it", ("str_timeliness" in by_key(comp["india"]), "Suspicious-report timeliness" in comp["message"]), (True, True))
    check("the message gives the counts", "Straight-through rate: 1 of 4" in comp["message"], True)
    page = await api.read(user=users["adv"], firm=firm, session=s)
    check("the page is read with the reader's role", "str_timeliness" in by_key(page["india"]), False)
    raci = {r["activity"]: r for r in comp["raci"]}
    check("the Indian matrix replaces the American one", ([x["key"] for x in comp["raci_columns"]], len(raci), "First investment" in raci),
          (["rm", "ops", "kyc_aml", "compliance", "platform"], 8, False))
    check("senior approval of a politically exposed person is compliance's, read from its tool", raci["PEP and high-risk approval"]["platform"], "Compliance")
    check("STR: the adviser is never involved, compliance records it", (raci["STR determination and filing"]["rm"], raci["STR determination and filing"]["platform"]),
          ("Never involved", "Compliance"))
    check("who records is read from the tools", raci["KYC status check and remediation guidance"]["platform"], "Adviser, Paraplanner, Operations, Compliance")
    check("the officers not recorded say so", [o["name"] for o in comp["india"]["officers"]], [None, None])
    american = await ToolExecutors(s, us_adv.id, us_adv.role, us.id).execute("read_onboarding_scoreboard", {})
    check("an American firm keeps its own", ("india" in american, "raci_columns" in american, "First investment" in [r["activity"] for r in american["raci"]]),
          (False, False, True))
    check("a client does not hold the scoreboard", UserRole.CLIENT in TOOLS["read_onboarding_scoreboard"].roles_required, False)

    print("\n=== The client's own reading ===")
    mine = await ex["client"].execute("read_my_onboarding_status", {})
    rows = {r["name"]: r for r in mine["accounts"]}
    check("their household's accounts only, and not the one that did not proceed", sorted(rows), ["Asha Sharma", "Hari Sharma", "Meera Sharma"])
    check("each step", {n: r["step"] for n, r in rows.items()}, {"Asha Sharma": "open", "Meera Sharma": "signature", "Hari Sharma": "with_firm"})
    check("what waits on them", rows["Meera Sharma"]["needs"], ["Your signature on the agreement", "The name on a form does not match"])
    check("a file under review reads only as with the firm", (rows["Hari Sharma"]["step_label"], rows["Hari Sharma"]["needs"]), ("With the firm", []))
    text = json.dumps(mine).lower()
    check("nothing about screening, monitoring, risk, exceptions or who works it",
          [w for w in ("screen", "monitor", "pmla", "pep", "edd", "suspicious", "aml", "risk", "sla", "overdue", "sanction", "compliance", "listed", "ops@t",
                       "desk", "payer") if w in text], [])
    check("the card of what to say gives the step", "Meera Sharma (Joint, anyone or survivor): your signature" in mine["message"], True)
    shown = await canvas.canvas_onboarding(household_id=rao.id, user=users["client"], firm=firm, db=s)
    check("the client's page ignores another household asked for", (shown["household_id"], "Rao" in json.dumps(shown["accounts"])), (str(sharma.id), False))
    staff = await canvas.canvas_onboarding(household_id=rao.id, user=users["adv"], firm=firm, db=s)
    check("staff see the household they ask for", sorted(r["name"] for r in staff["accounts"]), ["Bala Rao", "Chitra Rao", "Gopal Rao", "Rao HUF"])
    check("an approved account with no opening recorded reads as being opened", next(r["step"] for r in staff["accounts"] if r["name"] == "Gopal Rao"), "opening")
    check("the client holds the read, which changes nothing and takes no id",
          ("read_my_onboarding_status" in CLIENT_TOOLS, TOOLS["read_my_onboarding_status"].change_state, TOOLS["read_my_onboarding_status"].inputs),
          (True, ToolChangeState.NO, []))
    check("only a client holds it", TOOLS["read_my_onboarding_status"].roles_required, {UserRole.CLIENT})
    check("outside India there is nothing to show", (await client_onboarding.for_client(s, us, sharma.id))["applies"], False)

    print("\n=== A minor's conversion, a year ahead ===")

    def born(majority: date) -> date:
        return date(majority.year - 18, majority.month, min(majority.day, 28))

    soon, later = today + timedelta(days=360), today + timedelta(days=420)
    s.add(Person(firm_id=firm.id, household_id=sharma.id, full_name="Nila Sharma", date_of_birth=born(soon)))
    s.add(Person(firm_id=firm.id, household_id=sharma.id, full_name="Om Sharma", date_of_birth=born(later)))
    await s.flush()
    out = await india_reminders.raise_due(s, firm)
    tasks = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.title.like("%reaches majority%")))).scalars().all()
    check("raised within the year, not before it", [t.title.split("India: ")[1].split(" reaches")[0] for t in tasks], ["Nila Sharma"])
    t = tasks[0]
    check("due on the birthday, for the adviser, with what it takes",
          (t.due_date, t.assigned_to, "fresh KYC" in t.detail and "bank account in their own name" in t.detail),
          (date(born(soon).year + 18, born(soon).month, born(soon).day), users["adv"].id, True))
    await india_reminders.raise_due(s, firm)
    check("once only", (await s.execute(select(func.count()).select_from(Task).where(Task.firm_id == firm.id, Task.title.like("%reaches majority%")))).scalar_one(), 1)
    check("the year is a calendar year, a leap day too", india_deadlines.year_before(date(2028, 2, 29)), date(2027, 3, 1))


async def demonstration(s) -> None:
    from seed import india_b392, india_demo

    print("\n=== The demonstration firm: every measure has something to show ===")
    firm = await india_demo.build(s, quotes=None)
    await s.commit()
    got = by_key(await m.measures(s, firm, UserRole.COMPLIANCE))
    check("every measure is assessed", [k for k, x in got.items() if not x["assessed"]], [])
    cy = got["cycle_time"]["data"]
    check("each cohort has opened accounts", [cy[k]["n"] >= 1 for k in ("resident", "nri", "entity")], [True, True, True])
    st = got["straight_through"]["data"]["all"]
    check("some went straight through and some did not", (0 < st["straight_through"] < st["opened"]), True)
    sc = got["screening"]["data"]
    check("a hit decided late and one still open past the line", (sc["timed"] > sc["within_sla"], sc["open_over_sla"] >= 1), (True, True))
    check("the programme's reports: one in time, one still within time", (got["str_timeliness"]["data"]["in_time"], got["str_timeliness"]["data"]["open"]), (1, 1))
    check("a list change still to be screened against", sc["rescreen_backlog"] > 0, True)
    investigations = (await s.execute(select(AmlInvestigation.household_id).where(AmlInvestigation.firm_id == firm.id))).scalars().all()
    check("no household has two monitoring cases or two reports", (len(investigations), len(set(investigations))), (2, 2))
    check("this seed writes no screened party", (await s.execute(select(func.count()).select_from(OnboardingParty).join(
        OnboardingCase, OnboardingCase.id == OnboardingParty.case_id).where(OnboardingCase.firm_id == firm.id, OnboardingCase.notes == india_b392.BY))).scalar_one(), 0)
    check("the demonstration's minor has her task", len((await s.execute(select(Task.id).where(
        Task.firm_id == firm.id, Task.title.like("%Diya Iyer reaches majority%")))).all()), 1)
    client = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.CLIENT))).scalars().first()
    mine = await ToolExecutors(s, client.id, client.role, firm.id).execute("read_my_onboarding_status", {})
    steps = {(r["name"], r["step"]) for r in mine["accounts"]}
    check("the client sees an account open and one waiting for a signature", (("Meera Sharma", "signature") in steps, any(r["step"] == "invested" for r in mine["accounts"])
          or any(r["opened_on"] for r in mine["accounts"])), (True, True))
    check("the seed adds nothing a second time", await india_b392.add(s, firm), 0)
    waiting = (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id, OnboardingCase.notes == india_b392.BY,
                                                            OnboardingCase.status == "intake"))).scalar_one()
    sent = waiting.agreement_sent_at
    for row in (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id, OnboardingCase.notes == india_b392.BY))).scalars().all():
        row.intake = {**row.intake, india_b392.MARK: (firm_time.today(firm) - timedelta(days=10)).isoformat()}
    await s.flush()
    moved = await india_b392.add(s, firm)
    check("a later run moves its own cases forward by the days since", (moved, (waiting.agreement_sent_at - sent).days), (7, 10))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
