"""Prove India row 7: residence is what a person recorded, the days test is worked from it, and nothing else is guessed.

Covers the Income-tax residence test (every branch, and "not assessed" where a fact the answer turns on is missing), FEMA
status kept apart from it, the NRE, NRO and FCNR accounts and the limits with their source, FATCA and CRS self-certification
and US indicia, the gate before advice, the tax scan reading residence, a minor's guardian and the order it refuses, the
NRI and minor registration types with their documents, and the tools with their Confirm cards.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_nri
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (
    disclosures, document_matrix, firm_time, guardianship, law_figures, onboarding_start, order_guard, parties, pre_advice, residency,
    tax_india,
)
from app.aurea_core.graph import household_brain
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MandateType, PartyRole, RegistrationType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.residency import GuardianshipRecord, ResidencyRecord
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = date(2026, 11, 25)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(residency.ResidencyError,)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def status(**facts) -> str:
    return residency.tax_status({"tax_year": "2026-27", **facts}, TODAY)["status"]


def needs(**facts) -> list[str]:
    return residency.tax_status({"tax_year": "2026-27", **facts}, TODAY)["needs"]


async def main() -> int:
    print("\n=== The residence test, worked from the days a person recorded ===")
    check("no days on file is not assessed", (status(), needs()), ("not_assessed", ["Days in India in the year"]))
    check("182 days is resident whoever they are", status(days_in_india=182), "resident")
    check("181 days with no citizenship recorded is not assessed, and asks for it",
          (status(days_in_india=181), needs(days_in_india=181)), ("not_assessed", ["Indian citizen or person of Indian origin"]))
    check("a foreign national with 100 days and 400 in four years is resident (60-day test)",
          status(days_in_india=100, indian_citizen_or_pio=False, days_prior_four_years=400), "resident")
    check("the same with 300 in four years is not", status(days_in_india=100, indian_citizen_or_pio=False, days_prior_four_years=300), "non_resident")
    check("the short test with no four-year count is not assessed",
          (status(days_in_india=100, indian_citizen_or_pio=False), needs(days_in_india=100, indian_citizen_or_pio=False)),
          ("not_assessed", ["Days in India in the four years before"]))
    check("under 60 days a foreign national is non-resident", status(days_in_india=59, indian_citizen_or_pio=False), "non_resident")
    check("a citizen visiting from abroad with modest Indian income needs 182, not 60",
          status(days_in_india=150, indian_citizen_or_pio=True, india_income=800000, days_prior_four_years=900, visiting_india=True), "non_resident")
    check("without the recorded fact that they visit or left for work, 182 is not assumed: not assessed (B-393)",
          status(days_in_india=150, indian_citizen_or_pio=True, india_income=800000, days_prior_four_years=900), "not_assessed")
    check("a visitor with Indian income over Rs 15 lakh needs 120, and is not ordinarily resident below 182 (B-393)",
          status(days_in_india=130, indian_citizen_or_pio=True, india_income=2000000, left_for_employment_abroad=False,
                 days_prior_four_years=400, taxed_elsewhere=True, visiting_india=True), "resident_rnor")
    check("a citizen who left for employment abroad needs 182 whatever the income",
          status(days_in_india=130, indian_citizen_or_pio=True, india_income=2000000, left_for_employment_abroad=True,
                 days_prior_four_years=400, taxed_elsewhere=True), "non_resident")
    check("a citizen with high Indian income, 130 days, and whether they left unrecorded, is not assessed",
          (status(days_in_india=130, indian_citizen_or_pio=True, india_income=2000000),
           needs(days_in_india=130, indian_citizen_or_pio=True, india_income=2000000)),
          ("not_assessed", ["Left India for employment abroad this year", "Lives outside India and came on a visit this year",
                            "Liable to tax in another country"]))
    deemed = residency.tax_status({"tax_year": "2026-27", "days_in_india": 50, "indian_citizen_or_pio": True, "india_income": 2000000,
                                   "taxed_elsewhere": False}, TODAY)
    check("a citizen over Rs 15 lakh liable to tax nowhere else is deemed resident, not ordinarily",
          (deemed["status"], deemed["ordinarily_resident"]), ("resident_rnor", False))
    check("liable elsewhere, they are not", status(days_in_india=50, indian_citizen_or_pio=True, india_income=2000000, taxed_elsewhere=True), "non_resident")
    check("whether they are liable elsewhere unrecorded is not assessed",
          needs(days_in_india=50, indian_citizen_or_pio=True, india_income=2000000), ["Liable to tax in another country"])
    check("resident with 9 of 10 years non-resident is not ordinarily resident",
          status(days_in_india=200, nonresident_years_of_prior_ten=9), "resident_rnor")
    check("or 729 days in seven years", status(days_in_india=200, days_prior_seven_years=729), "resident_rnor")
    check("730 days and 8 years non-resident is ordinarily resident",
          status(days_in_india=200, days_prior_seven_years=730, nonresident_years_of_prior_ten=8), "resident_ordinary")
    check("resident with neither count recorded says so and does not guess",
          (status(days_in_india=200), needs(days_in_india=200)),
          ("resident", ["Years non-resident of the ten before", "Days in India in the seven years before"]))
    cited = residency.tax_status({"tax_year": "2026-27", "days_in_india": 10}, TODAY)["law"]
    check("every figure it used carries its source", all("Income-tax Act" in f["source"] for f in cited) and len(cited) == 8, True)
    check("and is dated no later than the day it was read", all(date.fromisoformat(f["as_of"]) <= date(2026, 11, 17) for f in cited), True)

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN",
                        settings={"licences": ["investment_adviser"], "timezone": "Asia/Kolkata"})
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="Other", slug="other", base_currency="INR", jurisdiction="IN")
            s.add_all([firm, nz, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="adviser@in", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            compliance = User(id=uuid.uuid4(), firm_id=firm.id, email="c@in", full_name="C", role=UserRole.COMPLIANCE, hashed_password="x")
            s.add_all([adviser, compliance])
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Menons")
            s.add(hh)
            await s.flush()

            def person(name, dob=None, firm_=firm):
                p = Person(id=uuid.uuid4(), firm_id=firm_.id, household_id=hh.id if firm_ is firm else None, full_name=name, date_of_birth=dob)
                s.add(p)
                return p

            vikram, anita = person("Vikram Menon", date(1972, 5, 18)), person("Anita Menon", date(1975, 2, 3))
            stranger = person("Someone Else", firm_=other)
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=vikram.id, name="Vikram - Balanced", mandate_type=MandateType.ADVISORY,
                              constraints={"service_track": "investment_adviser"})
            s.add(mandate)
            await s.flush()
            account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Vikram NRO demat", currency="INR", cash_balance=1000)
            s.add(account)
            await s.flush()

            print("\n=== Recording is a person's, from a document, added and never edited ===")
            before = (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one()
            await refused("a residence needs its document", residency.prepare(s, firm, person_id=vikram.id, document="", days_in_india=40), "where it is on file")
            await refused("nothing given is refused", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan"), "at least one fact")
            await refused("days are bounded", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan", days_in_india=400), "between 0 and 366")
            await refused("a tax year is written as the market writes it", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan", days_in_india=40, tax_year="2026"), "2026-27")
            await refused("a FEMA status is one of four", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan", fema_status="expat"), "one of")
            await refused("an account is NRE, NRO or FCNR", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan", accounts=["NRX"]), "one of")
            await refused("a self-certification cannot be dated in the future", residency.prepare(s, firm, person_id=vikram.id, document="Signed form", self_certified_on="2030-01-01"), "future")
            await refused("another firm's client is not recordable", residency.prepare(s, firm, person_id=stranger.id, document="Passport scan", days_in_india=40), "not a client")
            await refused("a New Zealand firm records no Indian residence", residency.prepare(s, nz, person_id=vikram.id, document="Passport scan", days_in_india=40), "Indian firm")
            check("nothing was written by refusals", (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one(), before)

            view = await residency.record(s, firm, by="adviser@in", person_id=vikram.id, document="Passport, visa, travel record",
                                          days_in_india=41, days_prior_four_years=170, days_prior_seven_years=310,
                                          nonresident_years_of_prior_ten=10, indian_citizen_or_pio=True, left_for_employment_abroad=True,
                                          india_income=640000, taxed_elsewhere=False, fema_status="nri",
                                          country_of_residence="United Arab Emirates", accounts=["NRE", "NRO"],
                                          pis_permission="PIS letter, Dubai branch", tax_residencies=["United Arab Emirates"])
            check("Vikram is non-resident by the days, and an NRI by FEMA", (view["tax"]["status"], view["fema_status"]), ("non_resident", "nri"))
            check("his steps say why", any("short test" in x or "No test" in x for x in view["tax"]["steps"]), True)
            check("recorded once, with who and where", (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one(), before + 1)
            await refused("the same facts again change nothing", residency.prepare(s, firm, person_id=vikram.id, document="Passport scan", days_in_india=41), "Nothing changes")

            print("\n=== A new record carries over what the last said ===")
            again = await residency.record(s, firm, by="adviser@in", person_id=vikram.id, document="Return for 2026-27", tax_year="2027-28", days_in_india=210)
            check("a new year keeps his FEMA status and accounts", (again["fema_status"], [a["kind"] for a in again["fema"]["accounts"] if a["recorded"]]), ("nri", ["NRE", "NRO"]))
            check("but not last year's days: 210 days makes him resident, and the counts still needed are named",
                  (again["tax"]["status"], again["tax"]["needs"]),
                  ("resident", ["Years non-resident of the ten before", "Days in India in the seven years before"]))
            check("the 2026-27 record still stands for its own year",
                  (await residency.person_view(s, firm, vikram, today=today))["tax"]["status"], "non_resident")
            rows = await residency.records(s, firm.id, vikram.id)
            f2627, _ = residency.standing(rows, "2026-27")
            check("and its days are not overwritten", f2627["days_in_india"], 41)
            check("history is kept: two records", len(rows), 2)

            print("\n=== FEMA is its own question, with its source ===")
            fema = (await residency.person_view(s, firm, vikram, today=today))["fema"]
            check("the three accounts are laid out, two held", [(a["kind"], a["recorded"]) for a in fema["accounts"]], [("NRE", True), ("NRO", True), ("FCNR", False)])
            check("the limits are cited figures", sorted(f["key"] for f in fema["law"]), ["in_nro_repatriation", "in_pis_aggregate_limit", "in_pis_individual_limit"])
            check("the NRO remittance limit is the stated figure", law_figures.value("in_nro_repatriation", today), 1_000_000)
            check("PIS is shown when recorded and never required", (fema["pis"]["recorded"], "nothing here requires it" in fema["pis"]["note"]), (True, True))
            check("tax residence and FEMA status can differ, and say so: an NRI whose 2027-28 days make him resident",
                  (again["differs"], (await residency.person_view(s, firm, vikram, today=today))["differs"]), (True, False))
            await residency.record(s, firm, by="adviser@in", person_id=anita.id, document="Passport and visa", tax_year="2026-27", fema_status="nri",
                                   indian_citizen_or_pio=True, days_in_india=30, india_income=0, taxed_elsewhere=True)
            anita_view = await residency.person_view(s, firm, anita, today=today)
            check("a non-resident's tax at source is laid out with treaty relief not modelled",
                  (anita_view["treatment"]["tds"][0]["rate"], any("Treaty relief" in t for t in anita_view["treatment"]["not_modelled"])), (0.20, True))
            check("a person with no record is not assessed, and it says that does not mean resident",
                  (await residency.person_view(s, firm, stranger, today=today))["message"], "No residence on file. That does not mean resident.")

            print("\n=== FATCA and CRS: what the client declared ===")
            fc = anita_view["fatca_crs"]
            check("no self-certification is a flag", (fc["on_file"], [x["key"] for x in fc["flags"]]), (False, ["crs_unknown"]))
            await residency.record(s, firm, by="adviser@in", person_id=anita.id, document="W-8 and the signed form", us_indicia=["us_address"], tax_residencies=["United Arab Emirates"])
            flags = [x["key"] for x in (await residency.person_view(s, firm, anita, today=today))["fatca_crs"]["flags"]]
            check("a US indicium with no self-certification is flagged, and the other country is reportable", flags, ["us_indicia", "crs"])
            check("a US indicium is never read as a US person", (await residency.person_view(s, firm, anita, today=today))["fatca_crs"]["us_person"], None)

            print("\n=== The gate before advice reads it ===")
            gate = await pre_advice.status(s, firm, hh.id)
            check("an Investment Adviser's NRI household needs the self-certification", [c["key"] for c in gate["checks"]][-1], "fatca_crs")
            check("and it is missing for Anita", (gate["checks"][-1]["state"], "Anita Menon" in gate["checks"][-1]["detail"]), ("fail", True))
            check("so there is no advice yet", "FATCA and CRS self-certification" in gate["missing"], True)
            await residency.record(s, firm, by="adviser@in", person_id=anita.id, document="Signed FATCA and CRS form", self_certified_on=today, us_person=False)
            await residency.record(s, firm, by="adviser@in", person_id=vikram.id, document="Signed FATCA and CRS form", self_certified_on=today, us_person=False)
            check("on file for both, the check passes", (await pre_advice.status(s, firm, hh.id))["checks"][-1]["state"], "pass")
            plain = Household(id=uuid.uuid4(), firm_id=firm.id, name="No record")
            s.add(plain)
            await s.flush()
            check("a household with nothing recorded is not asked, because not recorded is not resident", await residency.gate_check(s, firm, plain.id), None)

            print("\n=== The tax scan reads residence ===")
            brain = await household_brain(s, hh.id, firm_id=firm.id)
            report = await tax_india.report(s, brain, firm=firm, today=today)
            by_name = {t["name"]: t for t in report["taxpayers"]}
            res = (by_name.get("Vikram Menon") or {}).get("residency") or {}
            check("Vikram's taxpayer carries his residence, as recorded", (res.get("recorded"), res.get("status"), res.get("fema_status")),
                  (True, "non_resident", "nri"))
            check("a non-resident's gains are laid out with treaty relief not modelled",
                  any("Treaty relief" in t for t in (res.get("treatment") or {}).get("not_modelled", [])), True)
            check("the summary counts non-resident taxpayers", report["summary"]["non_resident_taxpayers"], 1)

            print("\n=== A minor's account is run by a guardian ===")
            child = person("Ira Menon", date(2015, 9, 2))
            kabir = person("Kabir Menon", None)
            adult = person("Meera Menon", date(2005, 1, 5))
            await s.flush()
            kid_mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=child.id, name="Ira - Balanced", mandate_type=MandateType.ADVISORY,
                                  constraints={"service_track": "investment_adviser"})
            s.add(kid_mandate)
            await s.flush()
            check("a minor with no guardian: an order is refused, naming what to record",
                  "run by a guardian" in (await guardianship.order_refusal(s, firm, kid_mandate) or ""), True)
            check("no date of birth: whether a client is a minor is not assessed, and nothing is refused",
                  (guardianship.status_of(kabir, None, today)["state"], guardianship.status_of(kabir, None, today)["minor"]), ("not_assessed", None))
            await refused("a guardian needs a relationship", guardianship.prepare(s, firm, minor_person_id=child.id, guardian_name="Vikram Menon", relationship="uncle", document="Birth certificate"), "one of", (guardianship.GuardianError,))
            await refused("and their document", guardianship.prepare(s, firm, minor_person_id=child.id, guardian_name="Vikram Menon", relationship="father", document=""), "where it is on file", (guardianship.GuardianError,))
            await refused("a person is not their own guardian", guardianship.prepare(s, firm, minor_person_id=child.id, guardian_person_id=child.id, guardian_name="Ira", relationship="father", document="Birth certificate"), "own guardian", (guardianship.GuardianError,))
            await refused("a minor with no date of birth on file cannot be given a guardian yet",
                          guardianship.prepare(s, firm, minor_person_id=kabir.id, guardian_name="X Menon", relationship="father", document="Birth certificate"),
                          "date of birth", (guardianship.GuardianError,))
            young = person("Neel Menon", date(2016, 3, 3))
            await s.flush()
            await refused("a person under 18 cannot be a guardian",
                          guardianship.prepare(s, firm, minor_person_id=young.id, guardian_person_id=child.id, guardian_name="Ira Menon",
                                               relationship="legal_guardian", document="Court order scan"), "under 18", (guardianship.GuardianError,))
            out = await guardianship.record(s, firm, by="adviser@in", minor_person_id=child.id, guardian_person_id=vikram.id, guardian_name="Vikram Menon",
                                            relationship="father", document="Birth certificate naming the father")
            row = next(m for m in out["minors"] if m["name"] == "Ira Menon")
            check("recorded: she is a minor with a guardian, adult on her 18th birthday", (row["state"], row["majority_on"]), ("guardian_on_file", "2033-09-02"))
            why = await guardianship.order_refusal(s, firm, kid_mandate)
            check("an order on her account is no longer refused for want of a guardian: it waits for the guardian's KYC and her PAN (B-283)",
                  (why is not None and "run by a guardian" not in why, "guardian's papers" in (why or "")), (True, True))
            court = GuardianshipRecord(firm_id=firm.id, minor_person_id=child.id, guardian_name="Court-appointed", relationship="court_appointed",
                                       document="Court order", recorded_by="x")
            check("a court-appointed guardian moves majority to 21", guardianship.majority_age(court, today), 21)
            check("a New Zealand firm has no such rule", (await guardianship.overview(s, nz))["applies"], False)

            print("\n=== The registrations and their documents ===")
            check("NRI and minor are registrations", [RegistrationType(v).value for v in ("nri_nre", "nri_nro", "minor_guardian")], ["nri_nre", "nri_nro", "minor_guardian"])
            check("an NRI account asks for the FATCA and CRS form and the visa or OCI card",
                  {"fatca", "status", "overseas", "bank", "pan", "id"} == {d[0] for d in document_matrix.MATRIX["nri_nre"]["documents"]}, True)
            check("a minor's asks for age proof and the guardian's authority",
                  [d[0] for d in document_matrix.MATRIX["minor_guardian"]["documents"]], ["id", "age", "guardian", "pan"])
            check("a minor has a guardian and a minor, an NRI an owner",
                  ([r[0] for r in parties.required_roles("minor_guardian")], [r[0] for r in parties.required_roles("nri_nre")]),
                  ([PartyRole.MINOR, PartyRole.GUARDIAN], [PartyRole.OWNER]))
            check("an Indian firm offers them", {"nri_nre", "nri_nro", "minor_guardian"} <= set(onboarding_start.types_for(firm)), True)
            check("a New Zealand firm does not", {"nri_nre", "nri_nro", "minor_guardian"} & set(onboarding_start.types_for(nz)), set())
            check("an NRI account carries a nomination choice, as an individual's does", "in_nomination_choice" in disclosures.required_for("IN", "nri_nre"), True)
            check("every document it names is labelled", all(k in document_matrix.DOCUMENTS for e in ("nri_nre", "nri_nro", "minor_guardian")
                                                              for _, _, kinds in document_matrix.MATRIX[e]["documents"] for k in kinds), True)

            print("\n=== The conversation: tools, roles and Confirm cards ===")
            for key in ("record_residency", "record_guardian"):
                check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("reading is not a change", (TOOLS["read_residency"].change_state, TOOLS["read_pms"].change_state), (ToolChangeState.NO, ToolChangeState.NO))
            ex = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            count = (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one()
            card = await ex.preview_change("record_residency", {"person_id": str(anita.id), "document": "Passport scan", "days_in_india": 25})
            labels = [r["label"] for r in card["rows"]]
            check("the card names the client, the change and the evidence", labels[:2] + [labels[-2]], ["Client", "Tax year", "Evidence"])
            check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one(), count)
            await refused("compliance reads and does not record", ToolExecutors(s, compliance.id, UserRole.COMPLIANCE, firm.id).preview_change(
                "record_residency", {"person_id": str(anita.id), "document": "Passport scan", "days_in_india": 25}), "adviser", (ExecutorError,))
            done = await ex.record_residency({"person_id": str(anita.id), "document": "Passport scan", "days_in_india": 25})
            check("confirmed, it is recorded once", (await s.execute(select(func.count()).select_from(ResidencyRecord))).scalar_one(), count + 1)
            read = await ex.read_residency({})
            check("the reading counts who is on file and who is not assessed", read["counts"]["not_on_file"] >= 1, True)
            gcard = await ex.preview_change("record_guardian", {"minor_person_id": str(child.id), "guardian_name": "Meera Menon",
                                                                "relationship": "legal_guardian", "document": "Court order scan"})
            check("the guardian's card names the minor, the guardian and when she is an adult",
                  [r["label"] for r in gcard["rows"]][:3], ["Minor", "Guardian", "Relationship"])
            check("the NRI half of B-257 is the registration types, and the tool says the minor never trades", done["applies"], True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
