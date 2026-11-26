"""Prove India's polish row: reminders raised as tasks (B-247), an overdue or blocked KYC holding a household's book (B-273), a PMS
performance fee carried as its own line only on a person's confirmation (B-261), residence and guardian facts read from an onboarding case
(B-259), a minor's account at majority (B-262) and a segment's minimum in its own currency (B-255).

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_polish
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (case_facts, client_view, firm_time, guardianship, india_deadlines, india_reminders, kyc_india, order_guard, platform_admin,
                            pms)
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.india_polish import MajorityConversion, PmsFeeLine
from app.models.india_rem_d import PmsUnitValue
from app.models.onboarding import OnboardingCase
from app.models.residency import GuardianshipRecord, ResidencyRecord
from app.models.tenant import Firm, FirmSegment
from seed import india_demo, india_polish

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(ExecutorError, pms.PmsError, guardianship.GuardianError, case_facts.CaseFactsError)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            nz = Firm(slug="nz-polish", name="Aotearoa", jurisdiction="NZ", regulator="FMA", base_currency="NZD", settings={}, is_active=True)
            s.add(nz)
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            ex = {r: ToolExecutors(s, u.id, u.role, firm.id) for r, u in users.items()}

            async def tasks_of(**kw):
                q = select(Task).where(Task.firm_id == firm.id, Task.source == india_reminders.SOURCE)
                return (await s.execute(q)).scalars().all()

            print("\n=== B-247: a near or overdue date becomes one task for its owner ===")
            seeded = await tasks_of()
            titles = sorted(t.title for t in seeded)
            check("the seed raised one task for each near or overdue date", len(seeded) >= 6, True)
            check("the overdue cyber policy review is compliance's", next(t for t in seeded if "Cybersecurity" in t.title).assigned_to, users[UserRole.COMPLIANCE].id)
            check("a month's filing falling due within 14 days is compliance's", any(t.title.startswith("India: Complaint data") and t.assigned_to == users[UserRole.COMPLIANCE].id
                                                                                       for t in seeded), True)
            lakshmi_t = next(t for t in seeded if "Lakshmi Iyer" in t.title)
            check("a client's KYC goes to the household's recorded adviser, on the household", (lakshmi_t.household_id is not None, lakshmi_t.assigned_to is not None), (True, True))
            check("a policy not yet due raises none (outsourcing has no day, conflict of interest is a month off)",
                  any("Conflict" in t for t in titles) or any("Outsourcing" in t for t in titles), False)
            check("advance tax and the return are not raised: whether they apply is not assessed", any("Advance tax" in t or "Income-tax return" in t for t in titles), False)
            before = len(await tasks_of())
            again = await india_reminders.raise_due(s, firm)
            check("a second run raises nothing and counts what already has a task", (again["raised"], again["already"] >= 6, len(await tasks_of())), (0, True, before))
            first = (await tasks_of())[0]
            first.status = "done"
            await s.flush()
            check("a task someone closed is not raised again", (await india_reminders.raise_due(s, firm))["raised"], 0)
            check("reminders change no record: the filing calendar and KYC states are as they were",
                  (await kyc_india.overview(s, firm))["counts"]["overdue"], 1)
            nz_run = await india_reminders.raise_due(s, nz)
            check("a firm with no Indian licence raises nothing", (nz_run["applies"], nz_run["raised"]), (False, 0))
            v = await india_reminders.view(s, firm)
            check("the view says each reminder has its task and who owns it", (v["to_raise"], all(r["owner"] or r["scope"] for r in v["reminders"])), (0, True))
            # A new near date raises exactly one more.
            sub = next(p for p in people.values() if p.full_name == "Subir Banerjee")
            tool = ex[UserRole.COMPLIANCE]
            read = await tool.execute("read_india_reminders", {})
            check("the tool reads the same list and says tasks only", (read["applies"], "Tasks only" in read["message"]), (True, True))
            check("raising is a Confirm card, reading is not", (TOOLS["raise_india_reminders"].change_state, TOOLS["read_india_reminders"].change_state),
                  (ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.NO))
            await refused("with every date already raised, the card says nothing is left", tool.preview_change("raise_india_reminders", {}), "nothing to raise")
            await refused("an adviser does not raise the firm's reminders", ex[UserRole.ADVISER].execute("raise_india_reminders", {}))
            hh_sub = await s.get(Household, sub.household_id)
            existing = (await s.execute(select(Task).where(Task.title.like("%Subir Banerjee%"), Task.source == india_reminders.SOURCE))).scalars().all()
            check("a client whose KYC is only due is not raised: due warns", existing, [])

            print("\n=== B-273: an overdue or blocked KYC holds the household's book ===")
            from app.aurea_core import wash_sale

            by_house = {}
            for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all():
                hid = await wash_sale.household_of(s, m)
                if hid:
                    by_house.setdefault(hid, m)
            mand_of = {n: by_house.get(people[n].household_id) for n in people}     # a client's household's first mandate
            lak_m = mand_of["Lakshmi Iyer"]
            acct = (await s.execute(select(Account).where(Account.mandate_id == lak_m.id))).scalars().first()
            res = await order_guard.review(s, firm=firm, mandate=lak_m, account=acct, orders=[], instruments={}, unpriced=[])
            check("an order for a household with an overdue KYC is refused, naming the KYC and who records it",
                  ("KYC is overdue" in res["refused"][0], "record_kyc" in res["refused"][0], "adviser, paraplanner, operations or compliance" in res["refused"][0]), (True, True, True))
            rec = type("Rec", (), {"agent_key": "drift_rebalancing", "subject_type": "mandate", "subject_id": lak_m.id})()
            why = await kyc_india.refusal(s, firm, rec)
            check("a book-moving approval is refused the same way", (why or "").startswith("Lakshmi Iyer's KYC is overdue"), True)
            runtime_src = open("app/atlas/runtime.py", encoding="utf-8").read()
            check("and dismissing stays open (the gate sits under `if action != HumanAction.DISMISS`)",
                  runtime_src.index("if action != HumanAction.DISMISS") < runtime_src.index("kyc_india.refusal"), True)
            kap_m = mand_of.get("Nandini Kapoor")
            if kap_m is not None:
                why = await kyc_india.order_refusal(s, firm, kap_m)
                check("a blocked KYC holds orders", "blocked" in (why or ""), True)
            for name in ("Subir Banerjee",):
                m = mand_of.get(name)
                if m is not None:
                    check(f"a KYC that is only due ({name}) holds nothing", await kyc_india.order_refusal(s, firm, m), None)
            ela = mand_of.get("Elaine D'Souza")
            if ela is not None:
                check("none on file holds nothing: it does not mean none was done", await kyc_india.order_refusal(s, firm, ela), None)
            rajesh_m = mand_of.get("Rajesh Sharma")
            check("a verified KYC holds nothing", await kyc_india.order_refusal(s, firm, rajesh_m), None)
            check("a firm outside India is not read", await kyc_india.household_refusal(s, nz, lak_m.id), None)
            await kyc_india.record(s, firm, by="adviser@astra", person_id=people["Lakshmi Iyer"].id, document="KRA acknowledgement, re-KYC, filed",
                                   ckyc_number="12345678901234", kra_name="CVL KRA", kra_status="validated", kyc_completed_on=today - timedelta(days=10),
                                   verification_mode="aadhaar_ekyc", aadhaar_last4="4321", pan_aadhaar_linked=True, id_proof_kind="passport",
                                   id_proof_valid_to=today + timedelta(days=2000), risk_band="high")
            check("once a person records the refreshed KYC the hold lifts", await kyc_india.order_refusal(s, firm, lak_m), None)

            print("\n=== B-262: a minor reaches majority; conversion is a person's recorded step ===")
            tara = people["Tara Menon"] if "Tara Menon" in people else (await s.execute(select(Person).where(Person.full_name == "Tara Menon"))).scalar_one()
            g = await guardianship.standing(s, firm.id, tara.id)
            st = guardianship.status_of(tara, g, today)
            check("an adult with a guardian still on file is flagged, not converted", (st["state"], st["minor"]), ("attained_majority", False))
            check("her task asks the adviser to convert the account", any("Tara Menon is now an adult" in t.title for t in await tasks_of()), True)
            tara_m = (await s.execute(select(Mandate).where(Mandate.person_id == tara.id))).scalars().first()
            check("orders on her account are refused: the guardian's authority has ended", "guardian's authority has ended" in (await guardianship.order_refusal(s, firm, tara_m) or ""), True)
            item = next(i for i in (await india_deadlines.household_items(s, firm, tara.household_id))["items"] if i["kind"] == "majority" and "Tara" in i["label"])
            check("the countdown says now an adult: convert", (item["label"].endswith("convert the account"), item["state"]), (True, "overdue"))
            await refused("a conversion needs where it is filed", guardianship.prepare_conversion(s, firm, person_id=tara.id, document=""), "where it is on file")
            await refused("a minor has nothing to convert", guardianship.prepare_conversion(s, firm, person_id=people["Ira Menon"].id, document="Form filed"), "no minor's account")
            await refused("a conversion is not dated in the future", guardianship.prepare_conversion(s, firm, person_id=tara.id, document="Form filed", converted_on=(today + timedelta(days=1)).isoformat()), "future")
            await refused("nor before she reached majority", guardianship.prepare_conversion(s, firm, person_id=tara.id, document="Form filed", converted_on=(today - timedelta(days=400)).isoformat()), "before")
            check("only an adviser, paraplanner or operations records", TOOLS["record_majority_conversion"].roles_required, {UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
            check("it is a Confirm card", TOOLS["record_majority_conversion"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            card = await ex[UserRole.ADVISER].preview_change("record_majority_conversion", {"person_id": str(tara.id), "document": "Signed conversion form, adult's KYC, filed"})
            check("the card names the guardian whose authority ends and says nothing moves",
                  (any(r["label"] == "Guardian whose authority ends" and r["value"] == "Vikram Menon" for r in card["rows"]), any("no holding or cash moves" in str(r["value"]) for r in card["rows"])), (True, True))
            await refused("compliance does not convert an account", ex[UserRole.COMPLIANCE].execute("record_majority_conversion", {"person_id": str(tara.id), "document": "Form filed"}))
            nothing = (await s.execute(select(func.count()).select_from(MajorityConversion))).scalar_one()
            check("a card writes nothing by being shown", nothing, 0)
            done = await ex[UserRole.ADVISER].execute("record_majority_conversion", {"person_id": str(tara.id), "document": "Signed conversion form, adult's KYC, filed"})
            check("confirmed, it is recorded and her state reads converted", (done["message"].startswith("Recorded"), next(m for m in done["minors"] if m["name"] == "Tara Menon")["state"]),
                  (True, "converted"))
            check("and orders on her account are no longer held for it", await guardianship.order_refusal(s, firm, tara_m), None)
            await refused("a second conversion is refused", guardianship.prepare_conversion(s, firm, person_id=tara.id, document="Form filed"), "already converted")
            check("the guardian record is kept: added and never edited", (await s.execute(select(func.count()).select_from(GuardianshipRecord).where(GuardianshipRecord.minor_person_id == tara.id))).scalar_one(), 1)

            print("\n=== B-259: residence and guardian facts an onboarding case holds ===")
            case = (await s.execute(select(OnboardingCase).where(OnboardingCase.prospect_name == "Kabir Menon"))).scalar_one()
            kabir = people["Kabir Menon"]
            read = await case_facts.read(s, firm, case.id)
            check("the case holds a guardian, read as a father, with the case's own document as evidence",
                  (read["guardian"]["guardian_name"], read["guardian"]["relationship"], "guardian_proof" in read["guardian"]["evidence"]), ("Vikram Menon", "father", True))
            check("no residence fact is invented from a minor registration", read["residence"], None)
            before_g = (await s.execute(select(func.count()).select_from(GuardianshipRecord).where(GuardianshipRecord.minor_person_id == kabir.id))).scalar_one()
            check("reading writes nothing: Kabir still has no guardian on file", before_g, 0)
            card = await ex[UserRole.ADVISER].preview_change("record_case_facts", {"case_id": str(case.id)})
            check("the card shows the guardian and the evidence", (any(r["label"] == "Guardian" and r["value"] == "Vikram Menon" for r in card["rows"]),
                                                                     any(r["label"] == "Guardian evidence" for r in card["rows"])), (True, True))
            check("a card writes nothing", (await s.execute(select(func.count()).select_from(GuardianshipRecord).where(GuardianshipRecord.minor_person_id == kabir.id))).scalar_one(), 0)
            await refused("compliance does not record a guardian", ex[UserRole.COMPLIANCE].execute("record_case_facts", {"case_id": str(case.id)}))
            done = await ex[UserRole.ADVISER].execute("record_case_facts", {"case_id": str(case.id)})
            check("confirmed, the guardian is recorded and Kabir is a minor with a guardian",
                  (done["recorded"], (await guardianship.standing(s, firm.id, kabir.id)).guardian_name), (["guardian"], "Vikram Menon"))
            await refused("a second confirm finds it already on file", case_facts.prepare(s, firm, case_id=case.id), "already")
            # An NRE case with a country of residence.
            nri = Person(firm_id=firm.id, household_id=kabir.household_id, full_name="Dev Menon", segment="private_wealth", profile={}, kyc={})
            s.add(nri)
            await s.flush()
            c2 = OnboardingCase(firm_id=firm.id, prospect_name="Dev Menon", registration_type="nri_nre", status="approved",
                                intake={"registration": {"country_of_residence": "Singapore"}}, materialized={"person_id": str(nri.id)})
            s.add(c2)
            await s.flush()
            r2 = await case_facts.read(s, firm, c2.id)
            check("an NRE case reads its country and its NRE account, and no FEMA status or day count",
                  (r2["residence"]["facts"], "fema_status" in r2["residence"]["facts"], "days_in_india" in r2["residence"]["facts"]),
                  ({"country_of_residence": "Singapore", "accounts": ["NRE"]}, False, False))
            await refused("with no document in the case, the evidence must be named", case_facts.prepare(s, firm, case_id=c2.id), "where the residence evidence")
            ok = await ex[UserRole.OPERATIONS].execute("record_case_facts", {"case_id": str(c2.id), "document": "Passport and visa scans, filed"})
            rec = (await s.execute(select(ResidencyRecord).where(ResidencyRecord.person_id == nri.id))).scalars().all()
            check("confirmed with a named document, it is recorded as the person's residence record", (ok["recorded"], len(rec), rec[0].country_of_residence, rec[0].accounts),
                  (["residence"], 1, "Singapore", ["NRE"]))
            c3 = OnboardingCase(firm_id=firm.id, prospect_name="Not Yet", registration_type="minor_guardian", status="intake",
                                intake={"registration": {"guardian_name": "Someone Else", "guardian_relationship": "father"}}, materialized={})
            s.add(c3)
            await s.flush()
            await refused("a case with no client yet has nobody to record against", case_facts.prepare(s, firm, case_id=c3.id), "has not made its client")
            await refused("another firm's case is not found", case_facts.read(s, nz, case.id), "indian firm")

            print("\n=== B-261: a performance fee is its own line, only on confirmation ===")
            karthik_m = (await s.execute(select(Mandate).where(Mandate.person_id == people["Karthik Iyer"].id))).scalars().first()
            # This section is about the estimate on the book; the demonstration unit-value ledger (B-283) would give the exact fee instead.
            await s.execute(delete(PmsUnitValue))
            await s.flush()
            await refused("with no estimate above zero there is nothing to confirm", pms.prepare_fee_line(s, firm, mandate_id=karthik_m.id), "not assessed")
            original = pms.performance_fee
            pms.performance_fee = lambda agreement, periods, start, end: {"estimate": 12_345.67, "period_return": 0.04, "hurdle_for_period": 0.025,
                                                                           "high_water_mark_index": 1.0, "end_index": 1.04, "excess_return": 0.015, "months": 3}
            try:
                from app.aurea_core import billing

                pv = await billing.preview(s, firm.id, as_of=today)
                entry = next(e for e in pv["arrangements"] if e["mandate_id"] == str(karthik_m.id))
                check("before confirming, the preview shows the estimate and no line", (entry["pms_fee"]["performance"]["estimate"], entry["pms_fee"]["line"]), (12_345.67, None))
                amount_before = entry["amount"]
                check("raising and recording are for the portfolio team, operations or an adviser", TOOLS["confirm_pms_fee_line"].roles_required,
                      {UserRole.PORTFOLIO_TEAM, UserRole.OPERATIONS, UserRole.ADVISER})
                check("it is a Confirm card", TOOLS["confirm_pms_fee_line"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                card = await ex[UserRole.PORTFOLIO_TEAM].preview_change("confirm_pms_fee_line", {"mandate_id": str(karthik_m.id)})
                labels = {r["label"]: r["value"] for r in card["rows"]}
                
                check("the card shows the line in rupees, the fee cap and that nothing debits it",
                      ("12,346" in labels["Performance fee line"], "Not counted" in labels["Fee cap"], "Not here" in labels["Debited"]), (True, True, True))
                check("a card writes nothing", (await s.execute(select(func.count()).select_from(PmsFeeLine))).scalar_one(), 0)
                await refused("compliance does not confirm a fee line", ex[UserRole.COMPLIANCE].execute("confirm_pms_fee_line", {"mandate_id": str(karthik_m.id)}))
                done = await ex[UserRole.PORTFOLIO_TEAM].execute("confirm_pms_fee_line", {"mandate_id": str(karthik_m.id)})
                check("confirmed, the line is recorded with who confirmed it", (done["line"]["amount"], bool(done["line"]["confirmed_by"])), (12345.67, True))
                pv = await billing.preview(s, firm.id, as_of=today)
                entry = next(e for e in pv["arrangements"] if e["mandate_id"] == str(karthik_m.id))
                check("the bill carries it as a separate line, outside the amount and the fee cap",
                      (entry["pms_fee"]["line"]["amount"], entry["pms_fee"]["line"]["in_fee_cap"], entry["amount"] == amount_before, entry["pms_fee"]["line"]["debited"]),
                      (12345.67, False, True, False))
                await refused("a second confirmation for the period is refused", pms.prepare_fee_line(s, firm, mandate_id=karthik_m.id), "already confirmed")
                await refused("a client with no performance fee has nothing to carry", pms.prepare_fee_line(s, firm, mandate_id=rajesh_m.id), "not a pms client")
            finally:
                pms.performance_fee = original

            print("\n=== B-255: a segment's minimum is in its own currency ===")
            for slug, label, amount in (("private_wealth", "Private Wealth", 500_000), ("mass_affluent", "Mass Affluent", 100_000), ("next_gen", "Next Gen", None)):
                s.add(FirmSegment(firm_id=firm.id, slug=slug, label=label, fee_tier_bps=75, min_aum_usd=amount))
            await s.flush()
            check("the polish seed states the demonstration firm's minimums in rupees and counts what it changed", await india_polish.add_segments(s, firm), 2)
            segs = {x["slug"]: x for x in await platform_admin.list_segments(s, firm)}
            check("the Indian firm's minimum is in rupees, with the currency on the row", (segs["private_wealth"]["currency"], segs["private_wealth"]["min_aum_usd"]), ("INR", 50_000_000))
            check("a dollar figure on an Indian firm was cleared, not converted", (segs["next_gen"]["min_aum_usd"], segs["next_gen"]["currency"]), (None, "USD"))
            check("the admin table reads the currency", next(c for c in (await platform_admin.read_section(s, firm, "segments"))["columns"] if c["key"] == "currency")["label"], "In") \
                if hasattr(platform_admin, "read_section") else None
            hh = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Sharma Family"))).scalar_one()
            hh.segment = "unassigned"
            await s.flush()
            terms = await client_view.fee_terms(s, firm, hh.id)
            check("with no segment assigned, the balance reads against a segment stated in the firm's own money",
                  (terms.get("applicable_segment") or {}).get("min_aum_currency", "INR"), "INR")
            check("each tier on the fee schedule names its currency", {r["min_aum_currency"] for r in terms["fee_schedule"]} <= {"INR", "USD"}, True)
            usd_seg = FirmSegment(firm_id=nz.id, slug="private_wealth", label="Private Wealth", fee_tier_bps=75, min_aum_usd=500_000)
            s.add(usd_seg)
            await s.flush()
            check("a segment with no currency reads as US dollars: all the column ever held", platform_admin.segment_currency(usd_seg), "USD")
            admin = users[UserRole.ADMIN]
            out = await platform_admin.save_segment(s, firm, admin, slug="family_office", label="Family Office", fee_tier_bps=40, min_aum_usd=200_000_000)
            check("a minimum a person states now is in the firm's own money", out.record.currency, "INR")
            before = out.record.min_aum_usd
            await platform_admin.save_segment(s, firm, admin, segment_id=out.record.id, label="Family Office", min_aum_usd=250_000_000)
            check("changing it keeps the firm's currency", (out.record.currency, out.record.min_aum_usd != before), ("INR", True))
            plan = await platform_admin.prepare_segment(s, firm, slug="x_office", label="X", min_aum_usd=1_000_000)
            check("the Confirm card names the currency the minimum is stated in", any(n[0] == "The minimum is stated in" and n[1] == "INR" for n in plan.notes), True)

            print("\n=== The seed, a second time ===")
            check("the polish seed adds nothing a second time", await india_polish.add(s, firm), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
