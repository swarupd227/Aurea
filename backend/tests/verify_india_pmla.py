"""Prove the PMLA programme for India (B-390): Indian names compared with the father's name as a signal, a list change
that rescreens the book and shows its backlog's age, a politically exposed person held until senior approval, checklists
for a cash business and other wealth, reports to FIU-IND recorded by compliance, suspicious report steps tied to the
Principal Officer with their timeliness, and the demonstration firm's seeded cases.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_pmla
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (aml_review, aml_risk, counsel_review, firm_time, fiu_reports, gates, indian_names, law_figures,
                            pep, screening, screening_lists, txn_monitoring)
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_monitoring import AmlInvestigation
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.pmla import FiuReport, PepApproval, ScreeningListUpdate
from app.models.portfolio import Transaction
from app.models.tenant import Firm
from seed import india_demo, india_pmla

PASS, FAIL = [], []
NEW_TOOLS = ("record_pep_approval", "record_screening_list_update", "run_delta_rescreen", "record_party_father_name", "record_fiu_report")
NO_DISCLOSURES = {"outstanding": [], "required_count": 0, "delivered_count": 0}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, aml_review.AmlReviewError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rows_of(card: dict) -> dict:
    return {r["label"]: r["value"] for r in card["rows"]}


async def count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def main() -> int:
    print("\n=== Indian names are compared as they are written, and the father's name is a signal ===")
    check("a transliteration variant", indian_names.compare("Lakshman Chowdhury", "Laxman Choudhary")[0], "spelling variant")
    check("another", indian_names.compare("Prakash Aggarwal", "Prakash Agarwal")[0], "spelling variant")
    check("and another", indian_names.compare("Deepak Malhotra", "Dipak Malhotra")[0], "spelling variant")
    check("word order does not matter", indian_names.compare("Rathore Harish", "Harish Rathore")[0], "spelling variant")
    check("initials stand for given names", indian_names.compare("R K Verma", "Rakesh Kumar Verma")[0], "initials")
    check("a title is not part of a name", indian_names.compare("Shri Harish Rathore", "Harish Rathore")[0], "exact")
    check("a shared given name alone is no match", indian_names.compare("Prakash Sharma", "Prakash Agarwal")[0], None)
    check("nor a shared surname alone", indian_names.compare("Meera Sharma", "Rajesh Sharma")[0], None)
    check("father: match, variant, differs, unknown",
          (indian_names.father("Bhola Choudhary", "Bhola Choudhary"), indian_names.father("Bhola Chowdhury", "Bhola Choudhary"),
           indian_names.father("Gopal Chowdhury", "Bhola Choudhary"), indian_names.father(None, "Bhola Choudhary")),
          ("match", "spelling variant", "differs", "unknown"))
    listed = [{"name": "Laxman Choudhary", "father_name": "Bhola Choudhary", "dob": "1969-02-11", "category": "sanctions", "source": "list"}]
    hit = screening.screen("Lakshman Chowdhury", dob="1985-08-14", father_name="Gopal Chowdhury", entries=listed).hits[0]
    check("a variant is a hit even where the letters alone score under the line", (hit.matched_name, hit.score < 0.82), ("Laxman Choudhary", True))
    check("and a differing father's name is shown, not used to clear it", hit.discriminators,
          {"dob": "differs", "name": "spelling variant", "father_name": "differs"})
    check("a firm with no recorded list is screened as before", screening.screen("Lakshman Chowdhury").hits, [])
    check("the provider's own hits are unchanged", screening.screen("Viktor Sokolov", dob="1961-03-14").hits[0].discriminators, {"dob": "match"})

    print("\n=== The figures are assumed and unreviewed ===")
    for key, value in (("in_pml_str_working_days", 7), ("in_pml_cash_threshold", 1_000_000), ("in_pml_ctr_due_day", 15)):
        f = law_figures.get(key)
        check(f"{key} is a law figure marked assumed, on counsel's list",
              (f.value, f.source.startswith("ASSUMED"), key in counsel_review.india_keys()), (value, True, True))
    check("monitoring reads the same figures", (txn_monitoring.THRESHOLD["IN"], txn_monitoring.REGIMES["IN"]["working_days"]), (1_000_000, 7))

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN", settings={}, created_at=utcnow() - timedelta(days=100))
            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([firm, us])
            await s.flush()
            users = {}
            for key, role, of in (("ops", UserRole.OPERATIONS, firm), ("comp", UserRole.COMPLIANCE, firm), ("comp2", UserRole.COMPLIANCE, firm),
                                  ("adv", UserRole.ADVISER, firm), ("uscomp", UserRole.COMPLIANCE, us)):
                u = User(id=uuid.uuid4(), firm_id=of.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, u.firm_id) for k, u in users.items()}

            async def new_case(name: str, **party) -> tuple[OnboardingCase, OnboardingParty]:
                case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name=name, registration_type="individual", intake={})
                s.add(case)
                await s.flush()
                p = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=case.id, role="owner", legal_name=name, **party)
                screening_lists.stamp(p, await screening_lists.entries(s, firm.id), "x")
                p.screened_at = utcnow() - timedelta(days=9)
                s.add(p)
                await s.flush()
                return case, p

            async def gate(case, key):
                found = (await s.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
                from app.aurea_core import parties
                status = parties.completeness_for(case.registration_type, list(found), await aml_review.decisions_for(s, [p.id for p in found]), market="IN")
                out = gates.evaluate(case, party_status=status, disclosure_status=NO_DISCLOSURES, pep_status=await pep.gate(s, firm, case))
                return next((g for g in out["gates"] if g["key"] == key), None)

            print("\n=== A list change rescreens the book ===")
            case, party = await new_case("Dipak Malhotra", dob="1975-12-01")
            change = {"list_key": "uapa", "version": "N-14", "published_on": (firm_time.today(firm) - timedelta(days=3)).isoformat(),
                      "entries": ["Deepak Malhotra | Ram Prasad Malhotra | 1968-03-30 | from the notification"], "source_document": "Notification N-14, compliance drive"}
            await refused("an adviser does not record a list change", ex["adv"].preview_change("record_screening_list_update", change), "compliance or operations")
            await refused("a firm outside India keeps none", ex["uscomp"].preview_change("record_screening_list_update", change), "Indian firm")
            await refused("a change needs its document", ex["comp"].preview_change("record_screening_list_update", {**change, "source_document": ""}), "filed")
            await refused("a change is not dated ahead", ex["comp"].preview_change("record_screening_list_update", {**change, "published_on": "2099-01-01"}), "future")
            card = rows_of(await ex["comp"].preview_change("record_screening_list_update", change))
            check("the card names who is rescreened and who may match", (card["Parties rescreened"], "Dipak Malhotra / Deepak Malhotra" in card["Possible matches with the added names"]), ("1", True))
            check("showing it writes nothing", await count(s, ScreeningListUpdate), 0)
            out = await ex["comp"].record_screening_list_update(change)
            check("recording it rescreens the book in the same step", ([h["party"] for h in out["rescreen"]["new_hits"]], party.screening_status), (["Dipak Malhotra"], "blocked"))
            check("what the conversation records is never marked as demonstration data",
                  (await s.execute(select(ScreeningListUpdate.demonstration))).scalars().all(), [False])
            check("and leaves no backlog", out["screening_lists"]["backlog"]["count"], 0)
            await refused("nothing waiting, nothing to run", ex["comp"].preview_change("run_delta_rescreen", {}), "No party is waiting")
            await refused("the same change twice", ex["comp"].preview_change("record_screening_list_update", change), "already recorded")
            hit_id = {"party_id": str(party.id), "hit": "Deepak Malhotra", "decision": "false_positive", "rationale": "Birth date differs by seven years; different person."}
            await ex["ops"].record_screening_decision(hit_id)

            late = ScreeningListUpdate(created_at=utcnow(), firm_id=firm.id, list_key="pep", version="P-2", published_on=firm_time.today(firm) - timedelta(days=6),
                                       entries=[{"name": "Harish Rathore", "father_name": "Bhanwar Singh Rathore", "dob": "1964-05-19"}], removed=[],
                                       source_document="received, not yet screened", demonstration=False, recorded_by="t")
            s.add(late)
            await s.flush()
            waiting = (await ex["ops"].read_aml_reviews({}))["screening_lists"]["backlog"]
            check("a party not screened since a change is the backlog, aged from the day the list changed", (waiting["count"], waiting["oldest_age_days"]), (1, 6))
            card = rows_of(await ex["ops"].preview_change("run_delta_rescreen", {}))
            check("the delta rescreen card says who waits and how long", (card["Parties waiting"], card["Oldest change"]), ("1", "6 day(s) ago"))
            out = await ex["ops"].run_delta_rescreen({})
            check("run, nobody waits", out["screening_lists"]["backlog"]["count"], 0)
            check("and the decision made before still stands", next(h["state"] for h in out["hits"] if h["matched_name"] == "Deepak Malhotra"), "false_positive")

            print("\n=== The father's name ===")
            rcase, rathore = await new_case("Harish Rathore", dob="1964-05-19")
            check("unknown until recorded", rathore.screening_hits[0]["discriminators"]["father_name"], "unknown")
            father = {"party_id": str(rathore.id), "father_name": "Bhanwar Singh Rathore", "document": "Passport, family details page"}
            await refused("a document is needed", ex["adv"].preview_change("record_party_father_name", {**father, "document": ""}), "document")
            await refused("another firm's party", ex["uscomp"].preview_change("record_party_father_name", father), "not found")
            await ex["adv"].record_party_father_name(father)
            check("recorded, the party is screened again and the signal reads match", rathore.screening_hits[0]["discriminators"]["father_name"], "match")
            check("and it decides nothing", (await ex["ops"].read_aml_reviews({"case_id": str(rcase.id)}))["hits"][0]["state"], "open")

            print("\n=== Operations signs off nothing on an unrated client ===")
            unrated, _ = await new_case("Meena Pillai")
            unrated.edd_status = "edd_pending"
            await s.flush()
            cash = {"case_id": str(unrated.id), "decision": "approve", "wealth_type": "cash_business", "rationale": "Takings account for the wealth described.",
                    "evidence": ["business_registration: GST certificate", "accounts_and_returns: three years audited", "bank_statements: two years", "cash_reconciliation: takings tie to deposits"]}
            await refused("operations does not", ex["ops"].preview_change("sign_off_edd", cash), "not rated")
            await refused("a cash business needs every item", ex["comp"].preview_change("sign_off_edd", {**cash, "evidence": cash["evidence"][:2]}), "bank statements", "reconciled")
            await refused("other wealth has its own list", ex["comp"].preview_change("sign_off_edd", {**cash, "wealth_type": "other", "evidence": []}), "corroboration")
            check("compliance may", rows_of(await ex["comp"].preview_change("sign_off_edd", cash))["Wealth"], "cash business")
            unrated.aml_risk_tier = "medium"
            check("and operations may once it is rated medium", rows_of(await ex["ops"].preview_change("sign_off_edd", cash))["Risk"], "Medium")
            check("every wealth the rating knows but none is left without a checklist", sorted(set(aml_risk.WEALTH) - set(aml_review.CHECKLISTS)), [])

            print("\n=== A politically exposed person ===")
            mohan, _ = await new_case("Mohan Das")
            await ex["ops"].rate_aml_risk({"case_id": str(mohan.id), "residence_country": "IN", "wealth_source": "employment", "channel": "face_to_face"})
            check("rated low before anyone marks them", (mohan.aml_risk_tier, mohan.edd_status, await gate(mohan, "pep_senior_approval")), ("low", "cdd", None))
            await ex["comp"].record_kyc({"case_id": str(mohan.id), "party_name": "Mohan Das", "pep": True, "kra_status": "validated", "document": "KRA acknowledgement"})
            check("a KYC record marked PEP opens enhanced due diligence", mohan.edd_status, "edd_pending")
            risk = (await ex["comp"].read_aml_reviews({"case_id": str(mohan.id)}))["risk"]
            check("and floors the rating at high, naming where it came from",
                  (risk["model_band"], any("KYC record" in f["why"] for f in risk["factors"])), ("high", True))
            g = await gate(mohan, "pep_senior_approval")
            check("the activation gate fails until senior approval", (g["state"], g["blocks"]), ("fail", True))
            sign = {"case_id": str(mohan.id), "decision": "approve", "wealth_type": "employment", "rationale": "Salary accounts for the wealth.",
                    "evidence": ["employment_evidence: appointment order", "income_evidence: three returns"]}
            mohan.aml_risk_tier = "medium"
            await refused("operations never signs off an exposed person's file", ex["ops"].preview_change("sign_off_edd", sign), "politically exposed")
            approve = {"case_id": str(mohan.id), "decision": "approve", "approver": "R. Gupta, Designated Director", "reason": "Wealth explained; annual review set.",
                       "evidence": "Board note 7, compliance drive"}
            await refused("senior approval needs the rating high", ex["comp"].preview_change("record_pep_approval", approve), "rate the client")
            await ex["comp"].rate_aml_risk({"case_id": str(mohan.id)})
            await refused("and the source-of-wealth file signed off", ex["comp"].preview_change("record_pep_approval", approve), "sign off")
            await ex["comp"].sign_off_edd(sign)
            check("signed off, still held for senior approval", (await gate(mohan, "pep_senior_approval"))["state"], "fail")
            await refused("operations does not record it", ex["ops"].preview_change("record_pep_approval", approve), "compliance")
            await refused("it names the senior person", ex["comp"].preview_change("record_pep_approval", {**approve, "approver": ""}), "senior person")
            await refused("and where it is filed", ex["comp"].preview_change("record_pep_approval", {**approve, "evidence": ""}), "filed")
            card = rows_of(await ex["comp"].preview_change("record_pep_approval", approve))
            check("the card names the person, the approver and what follows", (card["Senior approver"], "KYC record" in card["Politically exposed"], "may open" in card["Effect"]),
                  ("R. Gupta, Designated Director", True, True))
            check("showing it writes nothing", await count(s, PepApproval), 0)
            out = await ex["comp"].record_pep_approval(approve)
            check("recorded, the gate passes", ((await gate(mohan, "pep_senior_approval"))["state"], out["pep"][0]["status"]), ("pass", "approved"))
            await refused("not twice", ex["comp"].preview_change("record_pep_approval", approve), "already approved")
            spouse = OnboardingParty(firm_id=firm.id, case_id=mohan.id, role="joint_owner", legal_name="Harish Rathore", screening_status="review",
                                     screening_hits=[{"matched_name": "Harish Rathore", "category": "pep", "score": 1.0}], screened_at=utcnow())
            s.add(spouse)
            await s.flush()
            await ex["ops"].record_screening_decision({"party_id": str(spouse.id), "hit": "Harish Rathore", "decision": "true_match", "rationale": "Father's name and birth date match the entry."})
            check("a true match not yet confirmed marks nobody", len((await pep.for_case(s, firm, mohan))["people"]), 1)
            await ex["comp"].record_screening_decision({"party_id": str(spouse.id), "hit": "Harish Rathore", "kind": "confirmation", "decision": "confirmed", "rationale": "Agreed."})
            check("an exposed person who appears after an approval is not covered by it", (await gate(mohan, "pep_senior_approval"))["state"], "fail")
            await ex["comp"].record_pep_approval({**approve, "decision": "decline", "reason": "The second relationship is not acceptable."})
            check("a decline keeps the account from opening", "declined" in (await gate(mohan, "pep_senior_approval"))["detail"], True)

            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Das Household")
            s.add(h)
            await s.flush()
            client = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Lata Das")
            s.add(client)
            await s.flush()
            await ex["comp"].record_kyc({"person_id": str(client.id), "pep": True, "document": "KRA acknowledgement"})
            entry = next(e for e in (await ex["comp"].read_aml_reviews({}))["pep"] if e["holder_id"] == str(client.id))
            check("a client with no onboarding case is listed, approval outstanding", (entry["status"], entry["account_open"]), ("outstanding", True))
            own = {**approve, "case_id": None, "client_id": str(client.id)}
            await refused("and needs the source of wealth with the approval", ex["comp"].preview_change("record_pep_approval", own), "source of wealth")
            await ex["comp"].record_pep_approval({**own, "source_of_wealth": "Inherited land, per the probate order and revenue records"})
            check("recorded", next(e for e in await pep.register(s, firm) if e["holder_id"] == str(client.id))["status"], "approved")

            print("\n=== The suspicious report is the Principal Officer's, and timed ===")
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=client.id, name="Das", mandate_type="advisory")
            s.add(m)
            await s.flush()
            today = firm_time.today(firm)
            inv = AmlInvestigation(firm_id=firm.id, household_id=h.id, detected_on=today - timedelta(days=30), opened_by="comp", steps=[])
            s.add(inv)
            await s.flush()
            formed = {"investigation_id": str(inv.id), "step": "suspicion_formed", "on": (today - timedelta(days=20)).isoformat(), "note": "Nothing on file explains the movements."}
            await refused("refused with no Principal Officer recorded", ex["comp"].preview_change("record_investigation_step", formed), "Principal Officer", "record_sebi_registration")
            await refused("and no monthly report either", ex["comp"].preview_change("record_fiu_report", {"kind": "ctr", "period": f"{today.replace(day=1) - timedelta(days=1):%Y-%m}", "reference": "ACK-1", "transactions": 0}), "Principal Officer")
            firm.settings = {"sebi_registration": {"principal_officer": "S. Iyer (PO)"}}
            await s.flush()
            await ex["comp"].record_investigation_step(formed)
            view = await ex["comp"].read_transaction_monitoring({})
            check("the step carries the officer", (view["investigations"][0]["principal_officer"], view["fiu"]["officers"]["principal_officer"]), ("S. Iyer (PO)", "S. Iyer (PO)"))
            check("past the line and unfiled reads overdue", (view["fiu"]["str"]["overdue"], view["fiu"]["str"]["in_time_pct"]), (1, 0.0))
            check("the missing officer is named", view["fiu"]["officers_missing"], ["designated director"])
            filing = {**formed, "step": "reported", "on": today.isoformat(), "reference": "STR-ACK-9", "note": "Filed with the acknowledgement on the drive."}
            card = rows_of(await ex["comp"].preview_change("record_investigation_step", filing))
            check("the card names the officer and counts the days against the line", (card["Principal Officer"], card["Working days since suspicion"].endswith("of 7 (assumed line)")),
                  ("S. Iyer (PO)", True))
            await ex["comp"].record_investigation_step(filing)
            late_view = (await ex["comp"].read_transaction_monitoring({}))["fiu"]["str"]
            check("filed after the line reads late", (late_view["late"], late_view["in_time"], late_view["items"][0]["working_days"] > 7), (1, 0, True))
            quick = AmlInvestigation(firm_id=firm.id, household_id=h.id, detected_on=today - timedelta(days=9), opened_by="comp", status="reported",
                                     suspicion_on=today - timedelta(days=7), reported_on=today - timedelta(days=4), report_reference="STR-2",
                                     steps=[{"step": "suspicion_formed", "on": "x"}, {"step": "reported", "on": "x"}])
            bare = AmlInvestigation(firm_id=firm.id, household_id=h.id, detected_on=today - timedelta(days=9), opened_by="comp", status="reported",
                                    suspicion_on=today, reported_on=today, report_reference="STR-3", steps=[{"step": "reported", "on": today.isoformat()}])
            s.add_all([quick, bare])
            await s.flush()
            figures = (await fiu_reports.overview(s, firm))["str"]
            check("in time over everything measured; a report with no recorded day of suspicion is not measured",
                  (figures["measured"], figures["in_time"], figures["in_time_pct"], figures["not_measured"]), (2, 1, 50.0, 1))
            us_view = await txn_monitoring.for_firm(s, us.id)
            check("a firm outside India has no FIU-IND section", us_view["fiu"], None)

            print("\n=== Monthly reports to FIU-IND ===")
            last = f"{today.replace(day=1) - timedelta(days=1):%Y-%m}"
            ctr = {"kind": "ctr", "period": last, "reference": "FINGATE-ACK-0042", "transactions": 3}
            await refused("compliance alone records one", ex["ops"].preview_change("record_fiu_report", ctr), "compliance")
            await refused("with the filing's reference", ex["comp"].preview_change("record_fiu_report", {**ctr, "reference": ""}), "reference")
            await refused("not for a month still running", ex["comp"].preview_change("record_fiu_report", {**ctr, "period": f"{today:%Y-%m}"}), "not ended")
            await refused("a CTR says how many it carried", ex["comp"].preview_change("record_fiu_report", {**ctr, "transactions": None}), "how many")
            card = rows_of(await ex["comp"].preview_change("record_fiu_report", ctr))
            check("the card names the officer and says the day is assumed", (card["Principal Officer"], "assumed" in card["Due"]), ("S. Iyer (PO)", True))
            check("showing it writes nothing", await count(s, FiuReport), 0)
            before = (await fiu_reports.overview(s, firm))["reports"]
            check("unfiled months are due or overdue", sorted({r["state"] for r in before} - {"due", "overdue"}), [])
            out = await ex["comp"].record_fiu_report(ctr)
            row = next(r for r in out["fiu"]["reports"] if r["period"] == last and r["kind"] == "ctr")
            check("recorded with its reference", (row["state"] in ("filed", "filed_late"), row["reference"], row["transactions"]), (True, "FINGATE-ACK-0042", 3))
            await refused("once a month and kind", ex["comp"].preview_change("record_fiu_report", ctr), "was recorded filed")
            await ex["comp"].record_fiu_report({"kind": "monthly", "period": last, "reference": "FINGATE-ACK-0043"})

            print("\n=== Who may, and never a client ===")
            check("each new tool pauses for a Confirm card", [TOOLS[k].change_state for k in NEW_TOOLS], [ToolChangeState.PAUSE_FOR_CONFIRMATION] * 5)
            check("none is a client's", any(UserRole.CLIENT in TOOLS[k].roles_required for k in NEW_TOOLS), False)
            check("an adviser records no approval, list change or report",
                  any(UserRole.ADVISER in TOOLS[k].roles_required for k in ("record_pep_approval", "record_screening_list_update", "run_delta_rescreen", "record_fiu_report")), False)

        db2 = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with db2.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session2 = async_sessionmaker(db2, expire_on_commit=False)
        async with Session2() as s:
            print("\n=== The demonstration firm ===")
            demo = await india_demo.build(s)
            await s.commit()
            again = await india_pmla.add(s, demo)
            check("added once", again, 0)
            reviews = await aml_review.for_firm(s, demo.id)
            states = sorted({h["state"] for h in reviews["hits"]})
            check("hits open, awaiting a second reviewer, true and false", states, ["awaiting_confirmation", "false_positive", "open", "true_match"])
            check("every demonstration list is marked invented", all(x["demonstration"] for x in reviews["screening_lists"]["lists"]), True)
            check("and each hit's list says so", all("invented" in (h["source"] or "") for h in reviews["hits"]), True)
            check("a false positive is waiting for QA", reviews["summary"]["qa_due"], 1)
            check("the list change waits four days", (reviews["summary"]["rescreen_backlog"], reviews["summary"]["rescreen_oldest_days"]), (7, 4))
            pep_rows = {e["case"]: (e["status"], e["edd_status"], e["needs"]) for e in reviews["pep"]}
            check("two exposed persons, at different steps", pep_rows,
                  {"Harish Rathore": ("outstanding", "edd_complete", ["senior approval"]),
                   "Sunita Bhattacharya": ("outstanding", "edd_pending", ["source-of-wealth file signed off", "senior approval"])})
            tiers = {c.prospect_name: c.aml_risk_tier for c in (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == demo.id))).scalars().all()
                     if c.prospect_name in india_pmla.CASES}
            check("ratings across the bands", (tiers["Ritu Saxena"], tiers["Deepak Malhotra"], tiers["Harish Rathore"]), ("low", "medium", "high"))
            mon = await txn_monitoring.for_firm(s, demo.id)
            status = sorted((i["household"], i["status"]) for i in mon["investigations"])
            check("one investigation reported, one open", status, [("The Banerjee Household", "open"), ("The D'Souza Household", "reported")])
            check("an alert waits to be decided", [a["household"] for a in mon["alerts"] if not a["decision"]], ["The Menon NRI Household"])
            t = mon["fiu"]["str"]
            check("timeliness: one in time, one open with three working days gone", (t["in_time"], t["open"], t["overdue"], t["in_time_pct"],
                  next(i["working_days"] for i in t["items"] if i["state"] == "open")), (1, 1, 0, 100.0, 3))
            check("reports recorded with references, the latest CTR not yet", (await count(s, FiuReport),
                  all("invented" in r["reference"] for r in mon["fiu"]["reports"] if r["reference"])), (3, True))
            comp = (await s.execute(select(User).where(User.firm_id == demo.id, User.role == UserRole.COMPLIANCE))).scalar_one()
            cx = ToolExecutors(s, comp.id, comp.role, demo.id)
            harish = next(e for e in reviews["pep"] if e["case"] == "Harish Rathore")
            await cx.record_pep_approval({"case_id": harish["case_id"], "decision": "approve", "approver": "R. Gupta, senior management",
                                          "reason": "Wealth explained; annual review.", "evidence": "Approval note, compliance drive"})
            ran = await cx.run_delta_rescreen({})
            check("the demonstration's rescreen finds the new variant", [h["party"] for h in ran["rescreen"]["new_hits"]], ["Deepak Malhotra"])

            print("\n=== Re-dated for the day it is shown ===")
            check("nothing to move on the day it was written", await india_pmla.redate(s, demo, firm_time.today(demo)), 0)
            later = firm_time.today(demo) + timedelta(days=14)
            check("two weeks on, the open scenarios move", await india_pmla.redate(s, demo, later) > 0, True)
            moved = (await s.execute(select(AmlInvestigation).where(AmlInvestigation.firm_id == demo.id, AmlInvestigation.status == "open"))).scalar_one()
            check("the open suspicion is three working days old again", fiu_reports.working_days(moved.suspicion_on, later), 3)
            moved.steps = [*moved.steps, {"step": "continuing_review", "on": later.isoformat(), "by": "a person"}]
            await s.flush()
            was = moved.suspicion_on
            await india_pmla.redate(s, demo, later + timedelta(days=14))
            check("one a person has acted on is left", moved.suspicion_on, was)
        await db2.dispose()
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
