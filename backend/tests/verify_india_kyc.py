"""Prove India's KYC is what a person recorded, and its state is worked out from that, never set.

A record is added and never edited; the Aadhaar number is never stored (a 12-digit number is refused, only the last four are kept);
the state is verified, due, overdue, blocked, not assessed or not on file, from the date, the KRA status, the proofs and the risk
band; a politically exposed person is never below high; the beneficial-owner test is the circular's and apart from CRS; and an
overdue or blocked KYC appears in the board's KYC queue and fails the activation gate, where one due or none on file only warns.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_kyc
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import onboarding as api
from app.aurea_core import aml_risk, firm_time, kyc_india as k, law_figures
from app.aurea_core.reviews import add_months
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_risk import AmlRiskRating
from app.models.enums import UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Household, LegalEntity, Person
from app.models.identity import User
from app.models.kyc import KycRecord
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TWELVE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")


class TWELVE:
    """A 12-digit number in some text, ignoring the ids, which are random and can be all digits."""

    @staticmethod
    def search(text):
        return _TWELVE.search(UUID.sub("", text))


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
        check(label, "accepted", "refused")
    except (k.KycError, ExecutorError) as exc:
        check(label, all(c.lower() in str(exc).lower() for c in contains), True)


def pure() -> None:
    print("\n=== The refresh period is the firm's, with a default that says where it is from ===")

    class F:
        settings: dict = {}

    check("high 2 years, medium 8, low 10 by default", {b: v["months"] for b, v in k.review_months(F).items()}, {"low": 120, "medium": 96, "high": 24})
    check("and the default says it is assumed, with SEBI setting no limit", ("Assumed" in k.REVIEW_SOURCE, "no limit" in k.REVIEW_SOURCE), (True, True))
    F.settings = {"kyc_review_months": {"high": 12}}
    check("the firm's own replaces it", (k.review_months(F)["high"], k.review_months(F)["medium"]["months"]),
          ({"months": 12, "source": "the firm's setting"}, 96))

    print("\n=== Who is a beneficial owner, and who a controlling person ===")
    owners = [{"name": "A", "share": 40, "role": "shareholder"}, {"name": "B", "share": 10, "role": "shareholder"},
              {"name": "C", "share": 10.5, "role": "shareholder"}, {"name": "D", "share": 26, "role": "shareholder"}]
    bo = k.beneficial_owners("company", owners)
    check("a company: more than 10%, so 10% exactly is not", bo["beneficial"], ["A", "C", "D"])
    check("controlling persons are more than 25%, a different list", bo["controlling"], ["A", "D"])
    check("the threshold and its rule are named", (bo["threshold"], "More than 10%" in bo["rule"]), (0.10, True))
    check("an unincorporated body: more than 15%", k.beneficial_owners("unincorporated", [{"name": "A", "share": 15}, {"name": "B", "share": 15.1}])["beneficial"], ["B"])
    trust = k.beneficial_owners("trust", [{"name": "Settlor", "share": 0, "role": "author"}, {"name": "T", "share": 0, "role": "trustee"},
                                          {"name": "P", "share": 0, "role": "protector"}, {"name": "Ben1", "share": 10, "role": "beneficiary"},
                                          {"name": "Ben2", "share": 9, "role": "beneficiary"}])
    check("a trust: author, trustee, protector and a beneficiary with 10%", trust["beneficial"], ["Settlor", "T", "P", "Ben1"])
    check("every beneficiary of a trust is a controlling person for CRS", trust["controlling"], ["Settlor", "T", "P", "Ben1", "Ben2"])
    check("an HUF's is its karta", k.beneficial_owners("huf", [{"name": "Ramesh", "role": "karta"}])["beneficial"], ["Ramesh"])
    check("no owners recorded is a need, not a pass", k.beneficial_owners("company", [])["needs"], ["the owners, from the shareholding or the deed"])
    check("no structure is a need", k.beneficial_owners(None, [])["needs"], ["structure"])
    check("the thresholds are law_figures', each with a source",
          all(law_figures.get(key).source for key in ("in_kyc_bo_company", "in_kyc_bo_unincorporated", "in_kyc_bo_trust", "in_kyc_crs_company")), True)
    check("an Aadhaar is masked to its last four", k.mask_aadhaar("1234"), "••••••••1234")


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                        settings={"timezone": "Asia/Kolkata"})
            other = Firm(id=uuid.uuid4(), name="Astra US", slug="us", jurisdiction="US", regulator="SEC", base_currency="USD", settings={})
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            users = {}
            for key, role in (("adv", UserRole.ADVISER), ("comp", UserRole.COMPLIANCE), ("pm", UserRole.PORTFOLIO_TEAM), ("client", UserRole.CLIENT)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            family = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Rao Family", values={})
            alien = Household(id=uuid.uuid4(), firm_id=other.id, name="Elsewhere", values={})
            s.add_all([family, alien])
            await s.flush()
            people = {}
            for n in ("Asha Rao", "Vikram Rao", "Meena Rao", "Dev Rao", "Isha Rao", "Kiran Rao", "Leela Rao", "Mohan Rao"):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=family.id, full_name=n, profile={})
                s.add(p)
                people[n.split()[0]] = p
            company = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=family.id, name="Rao Holdings Pvt Ltd", entity_type="company")
            trust = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=family.id, name="Rao Family Trust", entity_type="trust")
            stranger = Person(id=uuid.uuid4(), firm_id=other.id, household_id=alien.id, full_name="Not Ours", profile={})
            s.add_all([company, trust, stranger])
            await s.flush()
            ex = {key: ToolExecutors(s, u.id, u.role, firm.id) for key, u in users.items()}

            async def record(who, **kw):
                key = {"person_id": who.id} if isinstance(who, Person) else {"entity_id": who.id}
                return await k.record(s, firm, by="adviser@astra", document=kw.pop("document", "KRA acknowledgement, CVL"), **key, **kw)

            async def state(who):
                o = await k.overview(s, firm)
                return next(p for p in o["parties"] if p["name"] == (who.full_name if isinstance(who, Person) else who.name))

            good = dict(ckyc_number="12345678901234", kra_name="CVL KRA", kra_status="validated", kyc_completed_on=today - timedelta(days=200),
                        verification_mode="aadhaar_ekyc", aadhaar_last4="1234", pan_aadhaar_linked=True, id_proof_kind="passport",
                        id_proof_valid_to=today + timedelta(days=2000), risk_band="low")

            print("\n=== Nothing on file is not a no ===")
            o = await k.overview(s, firm)
            check("everyone starts as not on file, and it says that is not none", (o["counts"]["not_on_file"], "does not mean" in o["parties"][0]["message"]), (10, True))
            check("it says lookups are not connected", "not connected" in o["not_connected"], True)
            check("a firm outside India has no KYC here", (await k.overview(s, other))["applies"], False)

            print("\n=== What cannot be recorded ===")
            base = dict(person_id=people["Asha"].id, document="KRA acknowledgement")
            await refused("an Aadhaar number is refused, not stored", k.prepare(s, firm, **base, aadhaar_last4="1234 5678 9012"), "never stored")
            await refused("even in a note", k.prepare(s, firm, person_id=people["Asha"].id, document="Aadhaar 1234 5678 9012 scan", kra_status="validated"), "never stored")
            await refused("only four digits", k.prepare(s, firm, **base, aadhaar_last4="12"), "last four")
            await refused("a CKYC number is 14 digits", k.prepare(s, firm, **base, ckyc_number="1234"), "14 digits")
            await refused("a KRA status is one of four", k.prepare(s, firm, **base, kra_status="clear"), "one of")
            await refused("a mode is one of four", k.prepare(s, firm, **base, verification_mode="phone"), "one of")
            await refused("never dated in the future", k.prepare(s, firm, **base, kyc_completed_on=today + timedelta(days=2)), "future")
            await refused("no evidence", k.prepare(s, firm, person_id=people["Asha"].id, document="", kra_status="validated"), "where it is on file")
            await refused("no facts", k.prepare(s, firm, **base), "at least one fact")
            await refused("a person and an entity", k.prepare(s, firm, person_id=people["Asha"].id, entity_id=company.id, document="x" * 6, kra_status="validated"), "one person")
            await refused("another firm's client", k.prepare(s, firm, person_id=stranger.id, document="KRA ack", kra_status="validated"), "no such")
            await refused("a firm outside India", k.prepare(s, other, person_id=stranger.id, document="KRA ack", kra_status="validated"), "indian firm")
            await refused("a band that is not one", k.prepare(s, firm, **base, risk_band="extreme"), "low, medium or high")
            await refused("a trigger that is not one", k.prepare(s, firm, **base, trigger="birthday"), "one of")
            await refused("an XML dated after the KYC", k.prepare(s, firm, **base, kyc_completed_on=today - timedelta(days=5), aadhaar_xml_dated=today - timedelta(days=1)), "after the kyc")

            print("\n=== Verified, and the clock by band ===")
            await record(people["Asha"], **good)
            a = await state(people["Asha"])
            check("verified, with its next date from the low band (10 years)", (a["state"], a["due_on"]), ("verified", add_months(today - timedelta(days=200), 120).isoformat()))
            check("the Aadhaar shows its last four only", a["aadhaar"], "••••••••1234")
            check("the CKYC number and KRA are shown as recorded", (a["ckyc_number"], a["kra_label"]), ("12345678901234", "Validated"))
            check("the band came from the record", a["band_from"], "the KYC record")
            stored = (await s.execute(select(KycRecord))).scalars().all()
            check("no 12-digit number is stored anywhere in the row", any(TWELVE.search(json.dumps({c.name: str(getattr(r, c.name)) for c in KycRecord.__table__.columns if c.name != "ckyc_number"})) for r in stored), False)
            audit = (await s.execute(select(LedgerEntry).where(LedgerEntry.event_type == "kyc_recorded"))).scalars().all()
            check("the ledger has it, masked, with no number", (len(audit), any(TWELVE.search(json.dumps(e.content)) for e in audit)), (1, False))

            await record(people["Vikram"], **{**good, "risk_band": "medium", "kyc_completed_on": add_months(today, -90)})
            check("medium falls due at 8 years", (await state(people["Vikram"]))["due_on"], add_months(add_months(today, -90), 96).isoformat())
            await record(people["Meena"], **{**good, "risk_band": "high", "kyc_completed_on": add_months(today, -30)})
            m = await state(people["Meena"])
            check("high is overdue after 2 years", (m["state"], m["due_on"]), ("overdue", add_months(add_months(today, -30), 24).isoformat()))
            check("and says when it was due", "Re-KYC was due" in m["message"], True)
            await record(people["Dev"], **{**good, "risk_band": "high", "kyc_completed_on": add_months(today, -24) + timedelta(days=30)})
            check("within 60 days of its date it is due", (await state(people["Dev"]))["state"], "due")

            print("\n=== The KRA's status ===")
            await record(people["Isha"], **{**good, "kra_status": "on_hold"})
            i = await state(people["Isha"])
            check("On Hold is blocked: the client may not transact", (i["state"], "may not transact" in i["message"]), ("blocked", True))
            await record(people["Isha"], kra_status="rejected")
            check("Rejected is blocked: restart KYC", ("restart" in (await state(people["Isha"]))["message"], (await state(people["Isha"]))["state"]), (True, "blocked"))
            await record(people["Isha"], kra_status="validated")
            check("a later record clears it, and the first stays on file",
                  ((await state(people["Isha"]))["state"], len((await s.execute(select(KycRecord).where(KycRecord.person_id == people["Isha"].id))).scalars().all())), ("verified", 3))
            check("the latest record kept what the earlier ones said", (await state(people["Isha"]))["ckyc_number"], "12345678901234")
            await record(people["Kiran"], **{**good, "kra_status": "registered"})
            check("Registered is due: its documents were not verified at source", ((await state(people["Kiran"]))["state"], "not verified at source" in (await state(people["Kiran"]))["message"]), ("due", True))

            print("\n=== The proofs and what the circular asks ===")
            await record(people["Leela"], **{**good, "id_proof_valid_to": today - timedelta(days=3)})
            check("an expired identity proof is overdue", ((await state(people["Leela"]))["state"], "expired" in (await state(people["Leela"]))["message"]), ("overdue", True))
            await record(people["Leela"], id_proof_valid_to=today + timedelta(days=900), pan_aadhaar_linked=False)
            check("a PAN not linked to Aadhaar is due", ((await state(people["Leela"]))["state"], "not linked" in (await state(people["Leela"]))["message"]), ("due", True))
            await record(people["Leela"], pan_aadhaar_linked=True, verification_mode="ipv")
            check("in-person verification with no date is short", "no date" in (await state(people["Leela"]))["message"], True)
            await record(people["Leela"], ipv_on=today - timedelta(days=200))
            check("with its date it is verified again", (await state(people["Leela"]))["state"], "verified")
            await record(people["Leela"], verification_mode="video_kyc")
            check("video and e-KYC waive IPV: no date asked", (await state(people["Leela"]))["state"], "verified")
            await record(people["Leela"], aadhaar_xml_dated=today - timedelta(days=204))
            check("an Aadhaar XML 4 days old at KYC is short of the 3 days", "no older than 3" in (await state(people["Leela"]))["message"], True)
            await record(people["Leela"], aadhaar_xml_dated=today - timedelta(days=202))
            check("2 days old is within them", (await state(people["Leela"]))["state"], "verified")
            await record(people["Leela"], address_proof_kind="utility_bill", address_proof_dated=add_months(today - timedelta(days=200), -5))
            check("a utility bill older than 2 months at KYC needs an updated document", "deemed address proof" in (await state(people["Leela"]))["message"], True)
            await record(people["Leela"], address_proof_dated=today - timedelta(days=230))
            check("one within 2 months is fine", (await state(people["Leela"]))["state"], "verified")
            await record(people["Leela"], trigger="address_change", trigger_on=today - timedelta(days=10))
            lt = await state(people["Leela"])
            check("a change of address after the KYC asks for a refresh", (lt["state"], "Change of address" in lt["message"]), ("due", True))
            await record(people["Leela"], kyc_completed_on=today - timedelta(days=2), aadhaar_xml_dated=today - timedelta(days=3), address_proof_dated=today - timedelta(days=20))
            check("a new KYC after it clears the trigger, and the old one stays on file",
                  ((await state(people["Leela"]))["state"], len((await state(people["Leela"]))["triggers"])), ("verified", 1))

            print("\n=== The band: the firm's, the AML rating's, or none ===")
            await record(people["Mohan"], **{k2: v for k2, v in good.items() if k2 != "risk_band"})
            mo = await state(people["Mohan"])
            check("no band anywhere: the clock is not assessed, never defaulted to low", (mo["state"], mo["due_on"], "risk band" in mo["needs"]), ("not_assessed", None, True))
            await record(people["Mohan"], pep=True)
            mo = await state(people["Mohan"])
            check("a politically exposed person is never below high", (mo["band"], mo["band_from"]), ("high", "political exposure (never below high)"))
            await record(people["Asha"], risk_band="low", pep=True)
            check("even a low recorded band reads high", ((await state(people["Asha"]))["band"], (await state(people["Asha"]))["review_months"]), ("high", 24))
            await record(people["Asha"], pep=False)
            firm.settings = {**firm.settings, "kyc_review_months": {"low": 36}}
            check("the firm's own period applies", (await state(people["Asha"]))["due_on"], add_months(today - timedelta(days=200), 36).isoformat())
            firm.settings = {"timezone": "Asia/Kolkata"}

            print("\n=== An entity: its structure and who owns it ===")
            await record(company, **{**good, "structure": "company", "id_proof_kind": None, "id_proof_valid_to": None},
                         beneficial_owners_text="Asha Rao, 40, shareholder, yes; Vikram Rao, 10, shareholder, yes; Meena Rao, 9, shareholder, no; Dev Rao, 41, shareholder, no")
            c = await state(company)
            check("more than 10% only, with CRS controlling persons apart", (c["owners"]["beneficial"], c["owners"]["controlling"]), (["Asha Rao", "Dev Rao"], ["Asha Rao", "Dev Rao"]))
            check("a beneficial owner with no KYC on file is named", (c["state"], "Beneficial owner Dev Rao has no KYC on file" in c["message"]), ("due", True))
            await record(company, beneficial_owners_text="Asha Rao, 40, shareholder, yes; Dev Rao, 41, shareholder, yes")
            check("with theirs on file it is verified", (await state(company))["state"], "verified")
            await record(trust, **{**good, "structure": "trust"})
            tr = await state(trust)
            check("a trust with no owners recorded says what it needs and is not read as verified",
                  ("for the entity: the owners, from the shareholding or the deed" in tr["needs"], tr["owners"]["beneficial"], tr["state"]), (True, [], "not_assessed"))

            print("\n=== The board's KYC queue and the activation gate ===")
            gcase = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Asha Rao", registration_type="individual", intake={"household": "Rao"},
                                   materialized={"person_id": str(people["Meena"].id)})
            s.add(gcase)
            await s.flush()
            states = await api.states_overview(s, firm)
            lines = [e["detail"] for e in states["queues"]["kyc"]["items"] if e["account"] == "Asha Rao"]
            check("an overdue KYC is in the KYC queue with its reason", any(l.startswith("KYC overdue, Meena Rao") and "Re-KYC was due" in l for l in lines), True)
            gate = next(g for g in (await api._gates_for(s, firm, gcase))["gates"] if g["key"] == "kyc_current")
            check("and it fails the activation gate, which then blocks", (gate["state"], gate["blocks"], gate["track"]), ("fail", True, "financial_crime"))
            gcase.materialized = {"person_id": str(people["Isha"].id)}
            check("a verified KYC passes the gate", next(g for g in (await api._gates_for(s, firm, gcase))["gates"] if g["key"] == "kyc_current")["state"], "pass")
            gcase.materialized = {"person_id": str(people["Dev"].id)}
            g = next(g for g in (await api._gates_for(s, firm, gcase))["gates"] if g["key"] == "kyc_current")
            check("one falling due only warns", (g["state"], g["blocks"]), ("warn", False))
            gcase.materialized = {}
            g = next(g for g in (await api._gates_for(s, firm, gcase))["gates"] if g["key"] == "kyc_current")
            check("none on file warns and says not on file is not not done", (g["state"], g["blocks"], "does not mean not done" in g["detail"]), ("warn", False, True))
            gcase.materialized = {"person_id": str(people["Mohan"].id)}
            s.add(AmlRiskRating(firm_id=firm.id, case_id=gcase.id, model_version="1.0", facts={}, factors=[], score=60, model_band="high", band="high",
                                review_months=12, next_review_on=today + timedelta(days=300), rated_by="comp@t", rated_by_role="compliance"))
            await s.flush()
            await record(people["Mohan"], pep=False, kyc_completed_on=add_months(today, -30))
            mo = next(p for p in (await k.case_status(s, firm, gcase))["parties"])
            check("with no band on the KYC, the AML rating of the account gives the clock", (mo["band"], mo["band_from"], mo["state"]), ("high", "the firm's AML rating", "overdue"))
            check("and an overdue one is on the gate again", next(g for g in (await api._gates_for(s, firm, gcase))["gates"] if g["key"] == "kyc_current")["state"], "fail")
            us_case = OnboardingCase(id=uuid.uuid4(), firm_id=other.id, prospect_name="US Client", registration_type="individual", intake={})
            s.add(us_case)
            await s.flush()
            check("a firm outside India has no such gate", "kyc_current" in [g["key"] for g in (await api._gates_for(s, other, us_case))["gates"]], False)

            print("\n=== The conversation's tools ===")
            check("a client holds none of them", [UserRole.CLIENT in TOOLS[t].roles_required for t in ("record_kyc", "read_kyc", "record_tax_loss", "record_grandfather_value", "read_india_tax_records")], [False] * 5)
            check("recording is gated by a Confirm card", all(TOOLS[t].confirmation for t in ("record_kyc", "record_tax_loss", "record_grandfather_value")), True)
            ask = {"person_id": str(people["Asha"].id), "document": "CKYC letter", "aadhaar_last4": "9876", "kra_status": "validated"}
            before = len((await s.execute(select(KycRecord))).scalars().all())
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_kyc", ask))["rows"]}
            check("the card names the client, masks the Aadhaar and says no lookup is made",
                  (card["Client"], card["Aadhaar, last four"].endswith("9876") and "•" in card["Aadhaar, last four"], "no KRA or CKYC lookup" in card["Note"]), ("Asha Rao", True, True))
            check("showing it writes nothing", len((await s.execute(select(KycRecord))).scalars().all()), before)
            await refused("the card refuses a whole Aadhaar number", ex["adv"].preview_change("record_kyc", {**ask, "aadhaar_last4": "123456789012"}), "never stored")
            await refused("the portfolio team does not record KYC", ex["pm"].preview_change("record_kyc", ask), "recorded by")
            out = await ex["comp"].record_kyc(ask)
            check("compliance records it", (len((await s.execute(select(KycRecord))).scalars().all()), "Recorded" in out["message"]), (before + 1, True))
            read = await ex["adv"].read_kyc({"household_id": str(family.id)})
            check("reading it says what is not connected and that not on file is not none", ("not connected" in read["message"], "does not mean no KYC" in read["message"]), (True, True))
            nobody = await ex["adv"].read_kyc({})
            hit = TWELVE.search(json.dumps(nobody, default=str)); check("a result never carries a whole Aadhaar number", hit.group(0) if hit else None, None)

            loss = {"person_id": str(people["Asha"].id), "tax_year": "2023-24", "kind": "long_term", "amount": 150000, "return_filed_on": "2024-07-20",
                    "document": "ITR-2 acknowledgement"}
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_tax_loss", loss))["rows"]}
            check("the loss card names the taxpayer, the year and the carry rule", (card["Taxpayer"], card["Tax year"], "eight tax years" in card["Note"]), ("Asha Rao", "2023-24", True))
            await refused("the client's role cannot record a loss", ex["client"].preview_change("record_tax_loss", loss), "recorded by")
            out = await ex["adv"].record_tax_loss(loss)
            check("recorded", (out["taxpayers"][0]["name"], out["taxpayers"][0]["available"]["long_term"]), ("Asha Rao", 150000.0))
            read = await ex["pm"].read_india_tax_records({})
            check("the portfolio team reads the records and the table", (len(read["taxpayers"]), len(read["asset_classes"])), (1, 11))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
