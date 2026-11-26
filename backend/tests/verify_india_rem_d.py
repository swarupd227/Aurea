"""Prove the India remainders: a ticket-size category the household's assets do not reach is not bought (B-303), the SIF minimum per PAN and
an AIF's lower minimum for a manager's recorded staff (B-303), a PMS unit-value ledger with an exact high-water mark, TWRR and a verifier's
letter (B-283), the debit of a confirmed PMS fee line behind its gates (B-283), a minor's guardian KYC and PAN (B-283), the KYC review period
by band as an admin setting (B-283) and the real estate gains choice (B-274).

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_rem_d
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import Enum, String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (billing, firm_time, guardianship, kyc_india, law_figures, licences, manager_staff, order_guard, pan, platform_admin, pms,
                            pms_ledger, pre_advice, product_gates, shelf)
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.billing import BillingRun, Invoice
from app.models.consent_registry import ConsentRecord
from app.models.enums import AssetClass, MandateType, MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import AuditEvent, User
from app.models.india_polish import PmsFeeLine
from app.models.india_rem_d import ManagerAffiliation, PmsFeeDebit, PmsUnitValue
from app.models.portfolio import Instrument, Transaction
from app.models.tenant import Firm
from seed import india_demo, india_rem_d

PASS, FAIL = [], []
ERRORS = (manager_staff.StaffError, pms_ledger.LedgerError, platform_admin.AdminError, pan.PanError, guardianship.GuardianError, ExecutorError)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def truth(label: str, got) -> None:
    check(label, bool(got), True)


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ERRORS as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:130]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def value(day: date, nav: float, units: float | None = 1000.0) -> PmsUnitValue:
    return PmsUnitValue(as_of=day, nav_per_unit=nav, units=units)


async def new_holder(s, firm, household, name: str, *, cash: float, pan_value: str | None) -> tuple[Person, Mandate]:
    p = Person(firm_id=firm.id, household_id=household.id, full_name=name, date_of_birth=date(1980, 1, 1), pan=pan_value,
               kyc={"status": "verified"}, profile={"risk_profile": "balanced", "tax": {}})
    s.add(p)
    await s.flush()
    m = Mandate(firm_id=firm.id, person_id=p.id, name=f"{name} - test", mandate_type=MandateType.ADVISORY,
                suitability={"risk_profile": "balanced", "capacity_for_loss": "high", "values_exclusions": []}, constraints={"service_track": "investment_adviser"})
    s.add(m)
    await s.flush()
    s.add(Account(firm_id=firm.id, mandate_id=m.id, name=f"{name} demat", custodian="CDSL", currency="INR", cash_balance=cash, registration_type="individual"))
    await s.flush()
    return p, m


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
            sharma = homes["The Sharma Family"]
            menons = homes["The Menon Family"] if "The Menon Family" in homes else next(h for n, h in homes.items() if "Menon" in n)
            ops, pm, comp, adv, admin = (users[UserRole.OPERATIONS], users[UserRole.PORTFOLIO_TEAM], users[UserRole.COMPLIANCE], users[UserRole.ADVISER],
                                         users[UserRole.ADMIN])
            ex = lambda u: ToolExecutors(s, u.id, u.role, firm.id)  # noqa: E731

            print("\n=== B-303: the SIF minimum is read per PAN, else per household, and says which ===")
            fig = law_figures.value("in_sif_min_ticket", today)
            rich, rich_m = await new_holder(s, firm, sharma, "Holder With PAN", cash=400_000, pan_value="DEMPH0001Q")
            plain, plain_m = await new_holder(s, firm, sharma, "Holder Without PAN", cash=400_000, pan_value=None)
            empty, empty_m = await new_holder(s, firm, sharma, "Holder With Nothing", cash=0, pan_value="DEMPH0002R")
            sif = Instrument(firm_id=firm.id, symbol="REM-SIF1", name="Demonstration SIF 1 (invented)", asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                             currency="INR", values_tags={})
            s.add(sif)
            await s.flush()
            await shelf.record_decision(s, firm.id, by=comp.email, role="research_cio", symbol="REM-SIF1", status="approved", rationale="Research view, invented",
                                        document="Due-diligence paper (demonstration)", category="sif")
            check("the SIF minimum is Rs 10 lakh", fig, 1_000_000)
            line = await product_gates.line_for(s, firm, sif, household_id=sharma.id, person_id=rich.id)
            check("with a PAN recorded the holder's own assets are read: Rs 4 lakh is below the minimum", (line["state"], line["assets"]), ("below", 400_000.0))
            truth("the line says it is read per PAN, masked", "read per PAN" in line["said"] and "DEMPH0001Q" not in line["said"] and "•" in line["said"])
            line = await product_gates.line_for(s, firm, sif, household_id=sharma.id, person_id=plain.id)
            truth("with no PAN the household's assets are read, and the line says so", "read per household" in line["said"] and line["assets"] > 400_000)
            check("so the household's wealth opens it for the holder with no PAN", line["state"], "open")
            line = await product_gates.line_for(s, firm, sif, household_id=sharma.id, person_id=empty.id)
            check("a holder with a PAN and nothing on file needs a fact first, never a no", line["state"], "needs_fact")
            view = await product_gates.open_to(s, firm, sharma.id)
            by_name = {h["person"]: h for h in view["sif_by_holder"]}
            check("the household view lists the SIF read per holder with its basis", (by_name["Holder With PAN"]["per_pan"], by_name["Holder Without PAN"]["per_pan"]), (True, False))

            print("\n=== B-303: a category out of reach is barred for the rebalancer and a direct order alike ===")
            barred = await shelf.barred(s, firm, rich_m)
            truth("the shelf bars it for the holder whose PAN assets are under the minimum", sif.id in barred and "out of reach" in barred[sif.id])
            truth("it does not bar it for the holder read per household", sif.id not in await shelf.barred(s, firm, plain_m))
            truth("it does not bar it for a holder with no asset figure", sif.id not in await shelf.barred(s, firm, empty_m))
            stops, _ = await shelf.order_refusals(s, firm, rich_m, [{"side": "buy", "symbol": "REM-SIF1", "instrument_id": str(sif.id)}])
            truth("a buy is refused with the gate's sentence", stops and "takes" in stops[0] and "read per PAN" in stops[0])
            stops, _ = await shelf.order_refusals(s, firm, rich_m, [{"side": "sell", "symbol": "REM-SIF1", "instrument_id": str(sif.id)}])
            check("a sale is never stopped", stops, [])
            refusals, _named = await product_gates.order_review(s, firm, rich_m, [{"side": "buy", "symbol": "REM-SIF1"}], {"REM-SIF1": sif})
            check("the order review still gives its own sentence (the guard drops the duplicate)", len(refusals), 1)

            print("\n=== B-303: an AIF's lower minimum applies to a manager's own staff, recorded from a document ===")
            aif = Instrument(firm_id=firm.id, symbol="REM-AIF2", name=f"{india_rem_d.MANAGER} Fund II", asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                             currency="INR", values_tags={})
            other = Instrument(firm_id=firm.id, symbol="REM-AIF3", name="Another Manager Fund III (invented)", asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                               currency="INR", values_tags={})
            s.add_all([aif, other])
            await s.flush()
            karthik = people["Karthik Iyer"]
            check("the seed recorded Karthik as the manager's employee", await product_gates.employee_manager(s, firm, karthik.id, aif, today), india_rem_d.MANAGER)
            check("it applies to that manager's own product alone", await product_gates.employee_manager(s, firm, karthik.id, other, today), None)
            check("a person with no post recorded gets the standard minimum", await product_gates.employee_manager(s, firm, rich.id, aif, today), None)
            staff = product_gates.read(firm, "aif_2", 3_000_000, accredited_investor=False, today=today, employee_of=india_rem_d.MANAGER)
            plain_aif = product_gates.read(firm, "aif_2", 3_000_000, accredited_investor=False, today=today)
            check("Rs 30 lakh opens an AIF for the recorded employee and not for anyone else", (staff["state"], plain_aif["state"]), ("open", "below"))
            check("the employee read carries the lower figure and the manager", (staff["minimum"], staff["figure"], staff["employee_of"]),
                  (2_500_000.0, "in_aif_min_ticket_employee", india_rem_d.MANAGER))
            check("the lower minimum is for an AIF alone", product_gates.read(firm, "pms", 3_000_000, accredited_investor=False, today=today,
                                                                              employee_of=india_rem_d.MANAGER)["minimum"], 5_000_000.0)
            await refused("a role outside the recorders may not record a post", manager_staff.record(
                s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, person_id=rich.id, manager="Some Manager", document="Appointment letter, filed"), "adviser")
            await refused("a post needs its document", manager_staff.record(s, firm, UserRole.ADVISER, by=adv.email, person_id=rich.id, manager="Some Manager", document="x"), "filed")
            await refused("a post cannot start in the future", manager_staff.record(
                s, firm, UserRole.ADVISER, by=adv.email, person_id=rich.id, manager="Some Manager", document="Appointment letter, filed",
                effective_on=(today + timedelta(days=3)).isoformat()), "future")
            await refused("the same post is not recorded twice", manager_staff.record(
                s, firm, UserRole.ADVISER, by=adv.email, person_id=karthik.id, manager=india_rem_d.MANAGER, document="Appointment letter, filed"), "already recorded")
            await refused("a post that is not in force cannot be ended", manager_staff.record(
                s, firm, UserRole.ADVISER, by=adv.email, person_id=rich.id, manager="Some Manager", state="ended", document="Resignation letter, filed"), "no recorded post")
            card = await ex(adv).preview_change("record_manager_affiliation", {"person_id": str(rich.id), "manager": "Some Manager", "document": "Appointment letter, filed"})
            truth("the card names the person, the manager and what it changes", any(r["label"] == "Effect" for r in card["rows"]))
            done = await ex(adv).record_manager_affiliation({"person_id": str(rich.id), "manager": "Some Manager", "document": "Appointment letter, filed"})
            check("recorded through the tool", len([p for p in done["people"] if p["person"] == "Holder With PAN"]), 1)
            await ex(adv).record_manager_affiliation({"person_id": str(rich.id), "manager": "Some Manager", "state": "ended", "document": "Resignation letter, filed"})
            check("and ended, the latest row standing", await manager_staff.for_person(s, firm, rich.id, today), [])
            check("nothing is edited: the history keeps both rows", (await s.execute(select(func.count()).select_from(ManagerAffiliation).where(
                ManagerAffiliation.person_id == rich.id))).scalar(), 2)

            print("\n=== B-283: the unit-value ledger, the high-water mark and the returns ===")
            mandate = (await s.execute(select(Mandate).where(Mandate.person_id == karthik.id))).scalars().first()
            values = await pms_ledger.series(s, firm.id, mandate.id)
            check("the seed recorded thirteen month-end values", len(values), 13)
            view = (await pms_ledger.ledger_view(s, firm, mandate_id=mandate.id))["clients"][0]
            check("the high-water mark is the highest value on file, with its day", view["high_water_mark"]["nav_per_unit"], max(india_rem_d.NAVS))
            first_last = pms_ledger.twrr(values, values[0].as_of, values[-1].as_of)
            check("the time-weighted return is the chain of each month's change per unit", round(first_last["return"], 4),
                  round(india_rem_d.NAVS[-1] / india_rem_d.NAVS[0] - 1.0, 4))
            check("twelve steps are laid out, with no gap named", (first_last["months"], first_last["gaps"]), (12, []))
            gap = pms_ledger.twrr([value(date(2026, 1, 31), 100), value(date(2026, 4, 30), 110)], date(2026, 1, 1), date(2026, 5, 1))
            check("a month missing is named as a gap, and the chain across it stays exact", (len(gap["gaps"]), gap["return"]), (1, 0.1))
            check("fewer than two values is not assessed, never zero", pms_ledger.twrr([value(date(2026, 1, 31), 100)], date(2026, 1, 1), date(2026, 5, 1))["return"], None)
            check("a client with no value on file is not assessed",
                  [c["state"] for c in (await pms_ledger.ledger_view(s, firm))["clients"] if c["count"] == 0][:1] in (["not_assessed"], []), True)
            hw = pms_ledger.high_water_mark([value(date(2026, 1, 31), 100), value(date(2026, 2, 28), 120), value(date(2026, 3, 31), 110)], date(2026, 3, 31))
            check("the mark holds after a fall", (hw["nav_per_unit"], hw["as_of"]), (120.0, "2026-02-28"))
            check("and before it was reached it is the earlier peak", pms_ledger.high_water_mark(
                [value(date(2026, 1, 31), 100), value(date(2026, 2, 28), 120)], date(2026, 1, 31))["nav_per_unit"], 100.0)

            print("\n=== B-283: the fee line on the ledger, and the estimate still called an estimate ===")
            terms = SimpleNamespace(hurdle_rate=0.10, performance_rate=0.20, high_water_mark=True)
            series = [value(date(2026, 1, 31), 100), value(date(2026, 2, 28), 120), value(date(2026, 3, 31), 110), value(date(2026, 4, 30), 115)]
            under = pms_ledger.fee(terms, series, date(2026, 4, 1), date(2026, 4, 30))
            check("a value under the high-water mark earns no fee", (under["estimate"], under["exact"], under["high_water_mark_nav"]), (0.0, True, 120.0))
            terms_no = SimpleNamespace(hurdle_rate=0.10, performance_rate=0.20, high_water_mark=False)
            no_mark = pms_ledger.fee(terms_no, series, date(2026, 4, 1), date(2026, 4, 30))
            hurdle = 0.10 * 29 / 365
            check("without a mark the fee is the rate on what clears the hurdle, times the units", no_mark["estimate"],
                  round(0.20 * (115 - 110 * (1 + hurdle)) * 1000.0, 2))
            stale = pms_ledger.fee(terms, [series[0], series[3]], date(2026, 4, 1), date(2026, 4, 30))
            check("a value too long before the period opens does not stand for it: no ledger fee, and it says why", (stale["estimate"], stale["exact"], "opens" in stale["reason"]),
                  (None, False, True))
            check("no opening value at all is not exact", pms_ledger.fee(terms, [value(date(2026, 4, 30), 115)], date(2026, 4, 1), date(2026, 4, 30))["exact"], False)
            check("no closing value is not exact", pms_ledger.fee(terms, series[:3], date(2026, 4, 1), date(2026, 4, 30))["exact"], False)
            check("no units stated is not exact", pms_ledger.fee(terms, [value(date(2026, 3, 31), 110, None), value(date(2026, 4, 30), 115, None)],
                                                                date(2026, 4, 1), date(2026, 4, 30))["exact"], False)
            q_start, q_end, _ = pms.last_quarter(today)
            agreement = await pms.standing(s, firm.id, mandate.id)
            exact_fee = pms_ledger.fee(agreement, values, q_start, q_end)
            truth("the last quarter's fee is exact on the seeded ledger", exact_fee["exact"] and exact_fee["estimate"] > 0)
            pv = await billing.preview(s, firm.id, as_of=today)
            entry = next(e for e in pv["arrangements"] if e["mandate_id"] == str(mandate.id))
            perf = entry["pms_fee"]["performance"]
            truth("the billing preview says whether the fee is on the ledger or an estimate", perf.get("exact") is True or perf.get("basis_label") == "estimate")
            if perf.get("exact"):
                check("a ledger fee names its basis and keeps the earlier estimate beside it", ("previous_estimate" in perf, "Value per unit" in perf["basis"]), (True, True))
            else:
                truth("an estimate says what stopped the ledger", perf.get("ledger_reason"))
            check("the fee is never added to the bill's amount", perf["added_to_bill"], False)

            print("\n=== B-283: a unit value, recorded from the statement, behind its card ===")
            await refused("a value is the portfolio team's or operations'", pms_ledger.record_unit_value(
                s, firm, UserRole.ADVISER, by=adv.email, mandate_id=mandate.id, as_of=today, nav_per_unit=120, document="Statement, filed"), "portfolio team")
            await refused("a statement cannot be in the future", pms_ledger.record_unit_value(
                s, firm, UserRole.OPERATIONS, by=ops.email, mandate_id=mandate.id, as_of=today + timedelta(days=2), nav_per_unit=120, document="Statement, filed"), "future")
            await refused("a value is more than zero", pms_ledger.record_unit_value(
                s, firm, UserRole.OPERATIONS, by=ops.email, mandate_id=mandate.id, as_of=today, nav_per_unit=0, document="Statement, filed"), "more than zero")
            await refused("a value names its statement", pms_ledger.record_unit_value(
                s, firm, UserRole.OPERATIONS, by=ops.email, mandate_id=mandate.id, as_of=today, nav_per_unit=120, document="x"), "filed")
            await refused("a mandate that is not a PMS client is refused", pms_ledger.record_unit_value(
                s, firm, UserRole.OPERATIONS, by=ops.email, mandate_id=rich_m.id, as_of=today, nav_per_unit=120, document="Statement, filed"), "not a pms client")
            await refused("the same value is not recorded twice", pms_ledger.record_unit_value(
                s, firm, UserRole.OPERATIONS, by=ops.email, mandate_id=mandate.id, as_of=values[-1].as_of, nav_per_unit=float(values[-1].nav_per_unit),
                units=float(values[-1].units), document="Statement, filed"), "already recorded")
            inputs = {"mandate_id": str(mandate.id), "as_of": (values[-1].as_of + timedelta(days=1)).isoformat() if values[-1].as_of < today else today.isoformat(),
                      "nav_per_unit": 119.1, "units": 120000, "document": "The manager's statement, filed (demonstration)"}
            card = await ex(ops).preview_change("record_pms_unit_value", inputs)
            truth("the card says nothing is interpolated", any(r["label"] == "Note" and "never interpolated" in r["value"] for r in card["rows"]))
            before = (await s.execute(select(func.count()).select_from(PmsUnitValue))).scalar()
            done = await ex(ops).record_pms_unit_value(inputs)
            check("recorded through the tool", (await s.execute(select(func.count()).select_from(PmsUnitValue))).scalar(), before + 1)
            truth("the answer carries the ledger", done["applies"] and done["clients"][0]["count"] == 14)
            await s.commit()

            print("\n=== B-283: a verifier's letter, and the client's report ===")
            report = await pms.client_report(s, firm, mandate.id)
            check("the last quarter reads not verified: the seeded letter stops before it", (report["verification"]["state"], report["publishable"]), ("not_verified", False))
            truth("and the report says why", "not verified" in report["not_publishable_because"].lower())
            truth("the unit-value return is laid out beside it", report["unit_twrr"]["return"] is not None)
            letter = dict(strategy=agreement.strategy, verifier="A second verifier (invented)", period_start=agreement.signed_on, period_end=q_end,
                          letter_date=today, scope="The strategy's returns, net of fees (invented)", document="The verifier's letter, filed (demonstration)")
            await refused("a letter is compliance's, the portfolio team's or operations'", pms_ledger.record_verification(s, firm, UserRole.ADVISER, by=adv.email, **letter), "compliance")
            await refused("a strategy must be one on an agreement", pms_ledger.record_verification(s, firm, UserRole.COMPLIANCE, by=comp.email, **{**letter, "strategy": "Nothing"}),
                          "not a strategy")
            await refused("a letter cannot verify a period after its own date", pms_ledger.record_verification(
                s, firm, UserRole.COMPLIANCE, by=comp.email, **{**letter, "letter_date": q_end - timedelta(days=5)}), "after its own date")
            await refused("a withdrawal needs a verification to withdraw", pms_ledger.record_verification(
                s, firm, UserRole.COMPLIANCE, by=comp.email, **{**letter, "state": "withdrawn", "reason": "Superseded by a later letter"}),
                "no verification on file")
            card = await ex(comp).preview_change("record_pms_verification", {k: (v.isoformat() if isinstance(v, date) else v) for k, v in letter.items()})
            truth("the card shows the period and the effect", any(r["label"] == "Covers" for r in card["rows"]) and any(r["label"] == "Effect" for r in card["rows"]))
            await pms_ledger.record_verification(s, firm, UserRole.COMPLIANCE, by=comp.email, **letter)
            report = await pms.client_report(s, firm, mandate.id)
            check("with a letter covering the quarter the return may be stated", (report["verification"]["state"], report["publishable"], report["not_publishable_because"]),
                  ("verified", True, None))
            await pms_ledger.record_verification(s, firm, UserRole.COMPLIANCE, by=comp.email, **{**letter, "state": "withdrawn", "reason": "The verifier withdrew the letter"})
            report = await pms.client_report(s, firm, mandate.id)
            check("a withdrawal is a new row and the quarter reads not verified again", (report["verification"]["state"], report["publishable"]), ("not_verified", False))
            await s.commit()

            print("\n=== B-283: debiting a confirmed fee line, never by itself ===")
            line = (await s.execute(select(PmsFeeLine).where(PmsFeeLine.mandate_id == mandate.id))).scalars().first()
            if line is None:
                line = PmsFeeLine(firm_id=firm.id, mandate_id=mandate.id, period_start=q_start, period_end=q_end, amount=25_000, currency="INR",
                                  working={"basis": "estimate"}, confirmed_by="confirmer@test")
                s.add(line)
                await s.flush()
            line.confirmed_by = ops.email                     # the same person confirmed it, so a second person is needed
            run = BillingRun(firm_id=firm.id, as_of=today, status="issued")
            s.add(run)
            await s.flush()
            acc = (await s.execute(select(Account).where(Account.mandate_id == mandate.id))).scalars().first()
            acc.cash_balance = float(line.amount) + 100_000
            await s.flush()
            await refused("not by the person who confirmed the line", pms_ledger.debit_fee_line(s, firm, UserRole.OPERATIONS, by=ops.email, fee_line_id=line.id), "second person")
            await refused("not by a role that does not take fees", pms_ledger.debit_fee_line(s, firm, UserRole.ADVISER, by=adv.email, fee_line_id=line.id), "operations")
            await refused("not without an issued bill for the period", pms_ledger.debit_fee_line(s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, fee_line_id=line.id), "not issued")
            inv = Invoice(firm_id=firm.id, run_id=run.id, kind="invoice", invoice_number="INV-REM-0001", mandate_id=mandate.id, mandate_name=mandate.name,
                          household_id=karthik.household_id, period_start=line.period_start, period_end=line.period_end, amount=10_000, currency="INR", status="issued")
            s.add(inv)
            await s.flush()
            await refused("not without the fee-debit authority on the consent registry", pms_ledger.debit_fee_line(
                s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, fee_line_id=line.id), "authority to debit")
            consent = lambda kind: ConsentRecord(firm_id=firm.id, household_id=karthik.household_id, kind=kind, status="granted", given_by="Karthik Iyer",  # noqa: E731
                                                 given_on=today - timedelta(days=30), method="e_signature", evidence="Signed form, filed (demonstration)", recorded_by="test")
            s.add(consent("fee_debit"))
            await s.flush()
            await refused("nor without e-delivery consent", pms_ledger.debit_fee_line(s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, fee_line_id=line.id), "e-delivery")
            s.add(consent("e_delivery"))
            acc.cash_balance = 1_000
            await s.flush()
            await refused("nor for more than the cash there is: a shortfall is shown, nothing is sold", pms_ledger.debit_fee_line(
                s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, fee_line_id=line.id), "short by")
            acc.cash_balance = float(line.amount) + 100_000
            await s.flush()
            card = await ex(pm).preview_change("debit_pms_fee_line", {"fee_line_id": str(line.id)})
            truth("the card names the amount, the cash, what is held back and who confirmed", {"Amount", "Cash held", "Held back", "Line confirmed by"} <= {r["label"] for r in card["rows"]})
            cash_before = float(acc.cash_balance)
            done = await ex(pm).debit_pms_fee_line({"fee_line_id": str(line.id)})
            await s.refresh(acc)
            check("the amount leaves the mandate's cash once", round(cash_before - float(acc.cash_balance), 2), round(float(line.amount), 2))
            check("a fee transaction carries the line", (await s.execute(select(func.count()).select_from(Transaction).where(
                Transaction.account_id == acc.id, Transaction.txn_type == "fee"))).scalar() >= 1, True)
            check("the debit is on record", (await s.execute(select(func.count()).select_from(PmsFeeDebit).where(PmsFeeDebit.fee_line_id == line.id))).scalar(), 1)
            truth("the answer lists the line as debited", any(l["id"] == str(line.id) and l["debited"] for l in done["lines"]))
            await refused("a second time is refused: exactly once", pms_ledger.debit_fee_line(s, firm, UserRole.PORTFOLIO_TEAM, by=pm.email, fee_line_id=line.id), "already")
            await s.commit()

            print("\n=== B-283: a minor trades only with the guardian's KYC and the minor's PAN ===")
            vikram = people["Vikram Menon"]
            kid = Person(firm_id=firm.id, household_id=menons.id, full_name="Test Minor", date_of_birth=today - timedelta(days=365 * 10 + 20), kyc={"status": "verified"},
                         profile={"risk_profile": "balanced", "tax": {}})
            keeper = Person(firm_id=firm.id, household_id=menons.id, full_name="Test Guardian No KYC", date_of_birth=date(1975, 5, 5), kyc={"status": "verified"},
                            profile={"risk_profile": "balanced", "tax": {}})
            s.add_all([kid, keeper])
            await s.flush()
            kid_m = Mandate(firm_id=firm.id, person_id=kid.id, name="Test Minor - Savings", mandate_type=MandateType.ADVISORY,
                            suitability={"risk_profile": "balanced", "capacity_for_loss": "medium", "values_exclusions": []}, constraints={"service_track": "investment_adviser"})
            s.add(kid_m)
            await s.flush()
            await refused("a minor's PAN needs the guardian linked first", pan.record(s, firm, by=adv.email, person_id=kid.id, pan="DEMPT0001A",
                                                                                      source_document="PAN card scan, filed"), "guardian as a person")
            await guardianship.record(s, firm, by=adv.email, minor_person_id=kid.id, guardian_name="Name Only Guardian", relationship="father",
                                      document="Birth certificate naming the father (demonstration)")
            await refused("a guardian recorded by name alone cannot have a KYC record, and the order is refused naming it",
                          _expect(order_guard_refusal(s, firm, kid_m)), "by name alone")
            await refused("a minor's PAN still needs a guardian who is a person", pan.record(s, firm, by=adv.email, person_id=kid.id, pan="DEMPT0001A",
                                                                                              source_document="PAN card scan, filed"), "guardian as a person")
            await guardianship.record(s, firm, by=adv.email, minor_person_id=kid.id, guardian_person_id=keeper.id, guardian_name="Test Guardian No KYC",
                                      relationship="father", document="Birth certificate naming the father (demonstration)")
            await refused("a guardian with no KYC record of their own holds the account", _expect(order_guard_refusal(s, firm, kid_m)), "no kyc record of their own")
            await kyc_india.record(s, firm, by=adv.email, person_id=keeper.id, kra_status="validated", ckyc_number="12345678901234", kyc_completed_on=today - timedelta(days=60),
                                   verification_mode="video_kyc", aadhaar_last4="4321", pan_aadhaar_linked=True, risk_band="low",
                                   document="KRA acknowledgement, filed (demonstration)")
            await refused("with the guardian's KYC on file the missing PAN is named", _expect(order_guard_refusal(s, firm, kid_m)), "no pan is recorded")
            papers = await guardianship.papers(s, firm)
            row = next(r for r in papers["minors"] if r["name"] == "Test Minor")
            check("the papers read guardian KYC on file and no PAN, not ready", (row["guardian_kyc"], row["pan"], row["ready"]), ("on_file", None, False))
            await pan.record(s, firm, by=adv.email, person_id=kid.id, pan="DEMPT0001A", source_document="PAN card scan, filed (demonstration)")
            check("with both on file the order is no longer held for papers", await guardianship.order_refusal(s, firm, kid_m), None)
            row = next(r for r in (await guardianship.papers(s, firm))["minors"] if r["name"] == "Test Minor")
            check("and the papers read ready, the PAN masked", (row["ready"], "DEMPT0001A" in str(row)), (True, False))
            truth("the seed's minor with her father's KYC and her own PAN is ready", next(r for r in papers["minors"] if r["name"] == "Ira Menon")["ready"])
            check("an adult is never read as a minor: no refusal for a person with no date of birth", await guardianship.order_refusal(s, firm, rich_m), None)
            card = await ex(adv).execute("read_guardian_papers", {})
            truth("the tool lists who is waiting", "waiting" in card["message"] and "Kabir Menon" in card["waiting"])
            await s.commit()

            print("\n=== B-283: the KYC review period by band is the administrator's, with a reason ===")
            now = kyc_india.review_months(firm)
            check("the default is assumed and says so", (now["high"]["months"], now["high"]["source"]), (24, "assumed default"))
            await refused("a reason is asked for", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 12}), "why")
            await refused("only an administrator", ex(comp).preview_change("set_kyc_review", {"band": "high", "months": 12, "reason": "Tighter"}), "administrator")
            await refused("a band is one of three", ex(admin).preview_change("set_kyc_review", {"band": "extreme", "months": 12, "reason": "Tighter"}), "band is one of")
            await refused("months stay in range: too few", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 2, "reason": "Tighter"}), "between 6 and 120")
            await refused("months stay in range: too many", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 240, "reason": "Looser"}), "between 6 and 120")
            await refused("months are a whole number", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": "soon", "reason": "Tighter"}), "whole number")
            await refused("high is never reviewed less often than medium", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 100, "reason": "Looser"}),
                          "no less often")
            card = await ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 12, "reason": "The board wants an annual review"})
            labels = {r["label"] for r in card["rows"]}
            truth("the card shows the change, its source now and after, and the default", {"High months", "Source now", "Source after", "Default", "Reason"} <= labels)
            done = await ex(admin).set_kyc_review({"band": "high", "months": 12, "reason": "The board wants an annual review"})
            check("applied", (done["changes"][0]["from"], done["changes"][0]["to"]), (24, 12))
            await s.refresh(firm)
            now = kyc_india.review_months(firm)
            check("the clock reads the firm's setting and says so; the other bands stay assumed", (now["high"]["months"], now["high"]["source"], now["low"]["source"]),
                  (12, "the firm's setting", "assumed default"))
            event = (await s.execute(select(AuditEvent).where(AuditEvent.firm_id == firm.id, AuditEvent.event_type == "kyc.review_months_set"))).scalars().first()
            check("the reason is on the audit log", event.detail["reason"], "The board wants an annual review")
            await refused("already so", ex(admin).preview_change("set_kyc_review", {"band": "high", "months": 12, "reason": "Again"}), "already so")
            read = await ex(admin).execute("read_kyc_review_period", {})
            check("the read tool names each band's source", read["bands"]["high"]["source"], "the firm's setting")
            truth("the setting is reserved: the firm profile cannot write it around the card", "kyc_review_months" in platform_admin.RESERVED_SETTINGS)
            await s.commit()

            print("\n=== B-274: the real estate gains choice, from the conversation ===")
            r = await ex(adv).execute("read_real_estate_choice", {"acquired_on": "2015-04-01", "sold_on": "2026-06-01", "cost": 5_000_000, "proceeds": 12_000_000,
                                                                  "resident_individual_or_huf": "yes", "cii_acquired": 240, "cii_sold": 376})
            truth("a resident who bought before July 2024 gets the lower of the two, with both shown", r["tax_with_indexation"] is not None and r["tax"] == min(r["tax_without_indexation"], r["tax_with_indexation"]))
            r = await ex(adv).execute("read_real_estate_choice", {"acquired_on": "2015-04-01", "sold_on": "2026-06-01", "cost": 5_000_000, "proceeds": 12_000_000,
                                                                  "resident_individual_or_huf": "yes"})
            check("without the index values it is not assessed and says what it needs", (r["state"], bool(r["needs"])), ("not_assessed", True))
            r = await ex(adv).execute("read_real_estate_choice", {"acquired_on": "2015-04-01", "sold_on": "2026-06-01", "cost": 5_000_000, "proceeds": 12_000_000})
            check("without the seller's status nothing is assumed", (r["state"], "resident individual" in r["message"]), ("not_assessed", True))
            await refused("a sale before the purchase is refused", ex(adv).execute("read_real_estate_choice", {
                "acquired_on": "2026-06-01", "sold_on": "2015-04-01", "cost": 1, "proceeds": 2, "resident_individual_or_huf": "no"}), "before it was bought")

            print("\n=== The Licences payload, the tools and the seed ===")
            pack = await licences._india_pack(s, firm)
            truth("the page carries the ledger, the staff, the papers and the review period", all(k in pack for k in ("pms_ledger", "manager_staff", "guardian_papers", "kyc_review")))
            check("the ledger on the page lists the confirmed lines", "lines" in pack["pms_ledger"], True)
            for key in ("record_manager_affiliation", "record_pms_unit_value", "record_pms_verification", "debit_pms_fee_line", "set_kyc_review"):
                check(f"{key} pauses for confirmation", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            for key in ("read_manager_staff", "read_pms_ledger", "read_guardian_papers", "read_real_estate_choice", "read_kyc_review_period"):
                check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
            check("a client holds none of them", [k for k in ("record_manager_affiliation", "record_pms_unit_value", "record_pms_verification", "debit_pms_fee_line",
                                                              "set_kyc_review", "read_pms_ledger", "read_manager_staff", "read_guardian_papers") if UserRole.CLIENT in TOOLS[k].roles_required], [])
            check("the debit is operations' and the portfolio team's alone", {r.value for r in TOOLS["debit_pms_fee_line"].roles_required}, {"operations", "portfolio_team"})
            check("the KYC period is the administrator's alone", {r.value for r in TOOLS["set_kyc_review"].roles_required}, {"admin"})
            check("the seed adds nothing a second time", await india_rem_d.add(s, firm), 0)

            print("\n=== Column lengths: Postgres enforces String(n), SQLite does not ===")
            too_long = []
            for table in Base.metadata.sorted_tables:
                for col in table.columns:
                    limit = getattr(col.type, "length", None)
                    if not limit or not isinstance(col.type, String) or isinstance(col.type, Enum):
                        continue
                    longest = (await s.execute(select(func.max(func.length(col))))).scalar()
                    if longest is not None and longest > limit:
                        too_long.append(f"{table.name}.{col.name} {longest}>{limit}")
            check("no seeded or recorded string is longer than its column", too_long, [])
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def order_guard_refusal(s, firm, mandate) -> str | None:
    """The refusal a minor's mandate meets: guardianship's own, which the order guard puts first."""
    return await guardianship.order_refusal(s, firm, mandate)


async def _expect(coro):
    why = await coro
    if why:
        raise guardianship.GuardianError(why)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
