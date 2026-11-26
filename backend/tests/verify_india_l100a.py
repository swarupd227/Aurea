"""Prove the L100 India items: ticket-size gates (B-290), accredited investors (B-291), an adviser's fee terms since 2025 (B-292) and the
February rate card (B-300).

Covers each minimum as a law figure with its source and "not reviewed" by default; what opens at Rs 3 crore (SIF, PMS and AIF) and at less,
"needs a fact first" with no asset figure and never a no; a gated product refused for a household under the minimum, named when no figure is on
file, SIF waived for an accredited investor and sales never stopped; a certificate recorded from its document (added never edited, lapsed and
withdrawn read not accredited, the criteria laid out and never checked against wealth) and its effect on the fee cap; an invoice longer than a
year, or than the months recorded, held; a mode election and a switch that lifts the cap; a second-opinion fee within 2.5% that counts in what
the family is charged; the deposit on the registration record; the rate card's list, the awaiting-the-Budget mark and its end, and the February
task once a year; every refusal; and the tools and their cards.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_l100a
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (accredited, adviser_fees, compliance_calendar, counsel_review, fee_cap, firm_time, law_figures, licences,
                            product_gates, rate_card, shelf)
from app.aurea_core import billing as billing_core
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import AssetClass, MarketType, UserRole
from app.models.graph import Household, Mandate, Person
from app.models.identity import User
from app.models.india_gates import AccreditedInvestor, AdviserFeeRecord
from app.models.india_governance import FigureWatch
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from seed import india_demo, india_l100a

PASS, FAIL = [], []
ERRORS = (accredited.AccreditedError, adviser_fees.FeeTermError, counsel_review.CounselError, compliance_calendar.CalendarError, shelf.ShelfError,
          product_gates.GateError, ExecutorError)


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
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
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
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            sharma = homes["The Sharma Family"]
            nandini, subir, karthik = people["Nandini Kapoor"], people["Subir Banerjee"], people["Karthik Iyer"]      # Nandini: in force; Karthik: lapsed; Subir: recorded below
            money = lambda v: f"Rs {v:,.0f}"  # noqa: E731

            print("\n=== B-290: the minimums are law figures, unreviewed ===")
            figs = {k: law_figures.get(k, today) for k in ("in_sif_min_ticket", "in_pms_min_ticket", "in_aif_min_ticket", "in_aif_min_ticket_employee")}
            check("SIF Rs 10 lakh, PMS Rs 50 lakh, AIF Rs 1 crore, an AIF manager's own staff Rs 25 lakh",
                  tuple(f.value for f in figs.values()), (1_000_000, 5_000_000, 10_000_000, 2_500_000))
            check("each carries a source that says it was not re-read", all("re-read" in f.source for k, f in figs.items() if k != "in_pms_min_ticket"), True)
            check("the PMS minimum is the one pms.py reads: the same figure, not a copy", product_gates.CATEGORIES["pms"]["figure"], "in_pms_min_ticket")
            counsel = await counsel_review.overview(s, firm)
            states = {r["key"]: r["counsel"]["state"] for r in counsel["figures"]}
            check("every new figure reads not reviewed in the counsel tracker",
                  [states[k] for k in ("in_sif_min_ticket", "in_aif_min_ticket", "in_aif_min_ticket_employee", "in_accredited_criteria", "in_ia_advance_fee_months",
                                       "in_ia_second_opinion_cap", "in_ia_deposit_liquid_funds")], ["not_reviewed"] * 7)

            print("\n=== B-290: what opens at what assets (the L100 self-test) ===")
            rows = {r["category"]: r for r in (product_gates.read(firm, k, 30_000_000, accredited_investor=False, today=today) for k in product_gates.CATEGORIES)}
            check("Rs 3 crore opens SIF, PMS and every AIF category", sorted(k for k, r in rows.items() if r["state"] == "open"), sorted(product_gates.CATEGORIES))
            rows = {k: product_gates.read(firm, k, 4_000_000, accredited_investor=False, today=today) for k in product_gates.CATEGORIES}
            check("Rs 40 lakh opens SIF alone: PMS and AIF are below their minimum",
                  {k: r["state"] for k, r in rows.items()}, {"sif": "open", "pms": "below", "aif_1": "below", "aif_2": "below", "aif_3": "below"})
            check("a below-minimum sentence names the minimum and the assets", "₹50,00,000" in rows["pms"]["said"] and "₹40,00,000" in rows["pms"]["said"], True)
            rows = {k: product_gates.read(firm, k, None, accredited_investor=False, today=today) for k in product_gates.CATEGORIES}
            check("with no asset figure every category needs a fact first, never a no", {r["state"] for r in rows.values()}, {"needs_fact"})
            check("an accredited investor is exempt from the SIF minimum, even with nothing on file",
                  (product_gates.read(firm, "sif", None, accredited_investor=True, today=today)["state"], product_gates.read(firm, "sif", 500_000, accredited_investor=True, today=today)["waived"]),
                  ("open", True))
            check("but not from PMS or AIF", product_gates.read(firm, "pms", 500_000, accredited_investor=True, today=today)["state"], "below")
            check("Rs exactly at the minimum opens", product_gates.read(firm, "aif_2", 10_000_000, accredited_investor=False, today=today)["state"], "open")
            await refused("a category outside the five is refused", shelf.prepare_decision(
                s, firm.id, symbol="NIFTYBEES.NS", status="approved", rationale="Research view", document="DD paper 1", category="aif_9"), "category is one of")

            print("\n=== B-290: a household at its recorded assets ===")
            view = await product_gates.open_to(s, firm, sharma.id)
            check("the household's categories come from the managed book, held away and recorded cash", (view["assets"]["amount"] is not None, len(view["categories"])), (True, 5))
            amount = view["assets"]["amount"]
            check("every category is read against that figure",
                  all((c["state"] == "open") == (amount >= c["minimum"]) for c in view["categories"] if not c["waived"]), True)
            check("the employee minimum is shown, and applied only to a person recorded as a manager's staff (B-303)",
                  (view["employee_minimum"]["minimum"], "standard minimum applies" in view["employee_minimum"]["said"] or view["employee_minimum"]["recorded"]), (2_500_000.0, True))
            check("a category that opens is never a recommendation", "never a recommendation" in view["note"], True)
            fleet = await product_gates.for_firm(s, firm)
            check("the Licences payload lists every household with what opens to it", (len(fleet["households"]) >= 5, all("categories" in h for h in fleet["households"])), (True, True))

            print("\n=== B-290: a product in a category, bought through the order guard ===")
            sharma_person = people["Rajesh Sharma"]
            mandate = (await s.execute(select(Mandate).where(Mandate.person_id == sharma_person.id))).scalars().first()
            funds = {}
            for sym, cat in (("DEMO-AIF2", "aif_2"), ("DEMO-SIF1", "sif"), ("DEMO-PLAIN", None)):
                inst = Instrument(firm_id=firm.id, symbol=sym, name=f"Demonstration {sym} (invented)", asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                                  currency="INR", values_tags={})
                s.add(inst)
                await s.flush()
                funds[sym] = inst
                if cat:
                    await shelf.record_decision(s, firm.id, by="research@test", role="research_cio", symbol=sym, status="approved", rationale="Research view, invented",
                                                document="Due-diligence paper (demonstration)", category=cat)
            check("the shelf keeps the category the research team gave", [await product_gates.category_of(s, firm, funds[k].id) for k in funds], ["aif_2", "sif", None])
            later = await shelf.record_decision(s, firm.id, by="research@test", role="research_cio", symbol="DEMO-AIF2", status="watch", rationale="Under watch, invented",
                                                document="Review note (demonstration)")
            check("a later decision keeps the category unless another is named", later.category, "aif_2")
            orders = [{"side": "buy", "symbol": "DEMO-AIF2"}, {"side": "buy", "symbol": "DEMO-SIF1"}, {"side": "buy", "symbol": "DEMO-PLAIN"}]

            async def fixed(amount_):
                async def _inv(session, firm_, household_id):
                    return {"amount": amount_, "managed": amount_, "held_away": 0, "cash": 0, "basis": "test"}
                return _inv

            real_investable = product_gates.investable
            try:
                product_gates.investable = await fixed(4_000_000)
                gated, named = await product_gates.order_review(s, firm, mandate, orders, funds)
                check("under the minimum, the AIF buy is refused with the minimum and the assets; SIF opens; an ungated product is not read",
                      (len(gated), "DEMO-AIF2" in gated[0], "₹1,00,00,000" in gated[0], named), (1, True, True, []))
                product_gates.investable = await fixed(None)
                gated, named = await product_gates.order_review(s, firm, mandate, orders, funds)
                check("with no asset figure nothing is refused and each gated buy is named as needing a fact", (gated, len(named), all("needs a fact first" in n for n in named)), ([], 2, True))
                product_gates.investable = await fixed(30_000_000)
                check("at Rs 3 crore neither is stopped", await product_gates.order_review(s, firm, mandate, orders, funds), ([], []))
                product_gates.investable = await fixed(100_000)
                gated, _ = await product_gates.order_review(s, firm, mandate, [{"side": "sell", "symbol": "DEMO-AIF2"}], funds)
                check("a sale is never stopped by the gate", gated, [])
                line = await product_gates.line_for(s, firm, funds["DEMO-AIF2"], household_id=sharma.id, person_id=sharma_person.id)
                check("the product-fit line reads below, with its sentence", (line["state"], "takes" in line["said"]), ("below", True))
            finally:
                product_gates.investable = real_investable

            print("\n=== B-291: accredited investor, from the certificate ===")
            standing = await accredited.standing(s, firm.id)
            check("the seed's certificate stands for Nandini and a lapsed one for Karthik",
                  (accredited.state_of(standing[nandini.id], today), accredited.state_of(standing[karthik.id], today)), ("accredited", "lapsed"))
            check("a lapsed certificate reads not accredited", karthik.id in await accredited.accredited_persons(s, firm), False)
            view = await accredited.overview(s, firm)
            check("the criteria are laid out beside the law's, with the caution", ("confirm" in view["caveat"].lower(), view["criteria"]["key"], view["people"][0]["criteria"] != []),
                  (True, "in_accredited_criteria", True))
            check("no certificate on file does not read not accredited", "does not mean a person is not accredited" in view["note"], True)
            adviser = users[UserRole.ADVISER]
            base = dict(person_id=people["Meera Sharma"].id, certificate_ref="ACC/DEMO/TEST/1", agency="A demonstration agency (invented)",
                        valid_from=today - timedelta(days=10), valid_to=today + timedelta(days=300), criteria=["Net worth of Rs 7.5 crore, as the certificate states it"],
                        document="Accreditation certificate, filed in the KYC folder")
            await refused("a portfolio-team member does not record a certificate", accredited.prepare(s, firm, UserRole.PORTFOLIO_TEAM, **base), "records a certificate")
            await refused("a certificate with no criteria is refused: they are laid out, never worked out", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "criteria": []}), "criteria")
            await refused("a certificate with no agency is refused", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "agency": ""}), "agency")
            await refused("a certificate with no reference is refused", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "certificate_ref": ""}), "reference")
            await refused("a certificate that has not started is refused", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "valid_from": today + timedelta(days=2)}), "starts")
            await refused("a certificate that ends before it starts is refused", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "valid_to": today - timedelta(days=11)}), "run out before")
            await refused("no document, no certificate", accredited.prepare(s, firm, UserRole.ADVISER, **{**base, "document": ""}), "filed")
            await refused("a withdrawal with nothing on file is refused", accredited.prepare(s, firm, UserRole.ADVISER, person_id=people["Meera Sharma"].id, state="withdrawn", document="Notice, filed", reason="Withdrawn by the agency"), "no certificate")
            n = (await s.execute(select(func.count()).select_from(AccreditedInvestor))).scalar_one()
            await accredited.record(s, firm, UserRole.ADVISER, by=adviser.email, **base)
            check("a certificate is added, not edited", (await s.execute(select(func.count()).select_from(AccreditedInvestor))).scalar_one(), n + 1)
            await refused("the same certificate twice is refused", accredited.prepare(s, firm, UserRole.ADVISER, **base), "already recorded")
            check("Meera is now accredited", people["Meera Sharma"].id in await accredited.accredited_persons(s, firm), True)
            await refused("a withdrawal needs its reason", accredited.prepare(s, firm, UserRole.ADVISER, person_id=people["Meera Sharma"].id, state="withdrawn", document="Notice, filed"), "why")
            await accredited.record(s, firm, UserRole.COMPLIANCE, by=users[UserRole.COMPLIANCE].email, person_id=people["Meera Sharma"].id, state="withdrawn",
                                    document="The agency's notice, filed", reason="The agency withdrew it on review")
            check("a withdrawal takes the status away and the certificate stays on file", (people["Meera Sharma"].id in await accredited.accredited_persons(s, firm),
                  (await s.execute(select(func.count()).select_from(AccreditedInvestor))).scalar_one()), (False, n + 2))
            await accredited.record(s, firm, UserRole.ADVISER, by=adviser.email, **{**base, "certificate_ref": "ACC/DEMO/TEST/2", "valid_from": today - timedelta(days=400),
                                                                                    "valid_to": today - timedelta(days=1)})
            check("a certificate that ran out yesterday reads lapsed", accredited.state_of((await accredited.standing(s, firm.id))[people["Meera Sharma"].id], today), "lapsed")
            check("the same certificate read next year (a year past its end) is still not accredited",
                  accredited.state_of((await accredited.standing(s, firm.id))[nandini.id], today + timedelta(days=400)), "lapsed")

            print("\n=== B-291: the fee cap does not reach an accredited investor's mandates ===")
            await accredited.record(s, firm, UserRole.ADVISER, by=adviser.email, person_id=subir.id, certificate_ref="ACC/DEMO/TEST/3", agency="A demonstration agency (invented)",
                                    valid_from=today - timedelta(days=30), valid_to=today + timedelta(days=330),
                                    criteria=["Annual income of Rs 2 crore, as the certificate states it"], document="Accreditation certificate, filed in the KYC folder")
            kmandates = [str(m.id) for m in (await s.execute(select(Mandate).where(Mandate.person_id == subir.id))).scalars().all()]
            smandates = [str(m.id) for m in (await s.execute(select(Mandate).where(Mandate.person_id == karthik.id))).scalars().all()]
            ok_people = await accredited.accredited_persons(s, firm)
            capped, exempt = await fee_cap._capped(s, kmandates + smandates, ok_people)
            check("Subir's mandates are exempt and Karthik's lapsed one is capped", (sorted(exempt), sorted(capped)), (sorted(kmandates), sorted(smandates)))
            capped, exempt = await fee_cap._capped(s, kmandates + smandates, set())
            check("with no certificate standing, both are capped", (exempt, len(capped)), ({}, len(kmandates + smandates)))
            pv = await billing_core.preview(s, firm.id)
            iyer = [e for e in pv["arrangements"] if e["household_name"] == "The Banerjee Household" and e["mandate_id"] in kmandates]
            check("a bill for the accredited investor says so and carries no cap block",
                  (all(e["fee_cap"].get("exempt") for e in iyer), any(c["code"] == "sebi_fee_cap" for e in iyer for c in e["checks"]), "accredited" in (iyer[0]["fee_cap"]["said"] if iyer else "").lower()),
                  (True, False, True))
            await accredited.record(s, firm, UserRole.COMPLIANCE, by=users[UserRole.COMPLIANCE].email, person_id=subir.id, state="withdrawn", document="The agency's notice, filed",
                                    reason="The agency withdrew it on review")
            pv = await billing_core.preview(s, firm.id)
            after = [e for e in pv["arrangements"] if e["household_name"] == "The Banerjee Household" and e["mandate_id"] in kmandates]
            check("once withdrawn, the cap reaches the same bill again", all(not e["fee_cap"].get("exempt") for e in after if e.get("fee_cap")), True)
            lic = await licences.overview(s, firm)
            check("the Licences payload carries the accredited investors, the gates, the fee terms and the rate card",
                  (lic["accredited"]["counts"].get("accredited", 0) >= 1, lic["ticket_gates"]["applies"], lic["fee_terms"]["applies"], lic["rate_card"]["applies"]), (True, True, True, True))

            print("\n=== B-292: an invoice longer than a year is held ===")
            entry = {"period_start": "2026-04-01", "period_end": "2027-03-31"}
            none = {"advance": None}
            check("a full financial year in advance is within the year", adviser_fees.advance_check(entry, none, today), None)
            held = adviser_fees.advance_check({"period_start": "2026-04-01", "period_end": "2027-04-01"}, none, today)
            check("a year and a day is held, naming SEBI's twelve months", (held is not None, "12 months" in held), (True, True))
            sharma_terms = await adviser_fees.household_terms(s, firm, sharma.id)
            check("the agreement as recorded collects six months ahead", sharma_terms["advance"].advance_months, 6)
            check("a quarter is within six months", adviser_fees.advance_check({"period_start": "2026-10-01", "period_end": "2026-12-31"}, sharma_terms, today), None)
            held = adviser_fees.advance_check({"period_start": "2026-04-01", "period_end": "2027-03-31"}, sharma_terms, today)
            check("a year is held where the agreement records six months, and says so", (held is not None, "6 month" in held), (True, True))
            check("a period ending in a short month is counted in months, not days", adviser_fees.advance_check({"period_start": "2026-08-31", "period_end": "2027-02-27"}, sharma_terms, today), None)
            await refused("an agreement cannot be recorded as collecting 13 months in advance", adviser_fees.prepare(
                s, firm, UserRole.ADVISER, household_id=homes["The Iyer Family"].id, kind="advance", advance_months=13, document="Signed agreement, filed"), "at most 12")
            await refused("nor 0", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=homes["The Iyer Family"].id, kind="advance", advance_months=0, document="Signed agreement, filed"), "1 to 12")
            await refused("the same term twice is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="advance", advance_months=6, document="Signed agreement, filed"), "already")
            await refused("a client's adviser-only record needs a document", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="advance", advance_months=9, document=""), "document")
            await refused("a portfolio-team member does not record a fee term", adviser_fees.prepare(s, firm, UserRole.PORTFOLIO_TEAM, household_id=sharma.id, kind="advance", advance_months=9, document="Signed agreement, filed"), "records a fee term")
            await refused("a kind outside the three is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="rebate", document="Signed agreement, filed"), "one of")
            await refused("a future date is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="advance", advance_months=9, effective_on=today + timedelta(days=1), document="Signed agreement, filed"), "future")
            pv = await billing_core.preview(s, firm.id)
            sh = [e for e in pv["arrangements"] if e["household_name"] == "The Sharma Family"]
            sh = [e for e in sh if e.get("fee_cap")]
            check("the billing preview carries the fee terms beside the cap", (len(sh) > 0, all("fee_terms" in e["fee_cap"] and e["fee_cap"]["fee_terms"]["advance"]["advance_months"] == 6 for e in sh)), (True, True))
            check("the figures behind them are cited in the preview", {"in_ia_advance_fee_months", "in_ia_second_opinion_cap"} <= {f["key"] for f in sh[0]["fee_cap"]["figures"]}, True)

            print("\n=== B-292: the mode, and a switch within the year ===")
            check("the seed's election and its earlier switch are on file", (sharma_terms["mode"].mode, [r.mode for r in sharma_terms["switches"]]), ("aua", ["aua"]))
            check("a switch before this financial year does not change this year's cap", sharma_terms["switched_on"], None)
            fixed_cap, rate = law_figures.value("in_ia_fee_fixed_cap", today), law_figures.value("in_ia_fee_aua_cap", today)
            check("without a switch the cap follows the modes billed", fee_cap.cap_for({"flat_fee"}, 10_000_000, today)[0], fixed_cap)
            cap, why = fee_cap.cap_for({"flat_fee"}, 10_000_000, today, switched_on=today - timedelta(days=5))
            check("after a switch within the year the cap is the higher of the two", (cap, why.startswith("after a switch of mode")), (max(fixed_cap, rate * 10_000_000), True))
            check("and the higher is the fixed cap when the AUA's share is smaller", fee_cap.cap_for({"aua"}, 1_000_000, today, switched_on=today)[0], fixed_cap)
            await refused("the same mode is not a switch", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="mode", mode="aua", document="Election, filed"), "already")
            await refused("a mode that is neither is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="mode", mode="hybrid", document="Election, filed"), "aua or fixed")
            await refused("a switch needs the adviser's reason", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="mode", mode="fixed", document="Election, filed"), "why")
            await refused("a switch cannot start before the election it replaces", adviser_fees.prepare(
                s, firm, UserRole.ADVISER, household_id=sharma.id, kind="mode", mode="fixed", effective_on=today - timedelta(days=500), reason="Client asked to go back", document="Election, filed"), "after the election")
            await adviser_fees.record(s, firm, UserRole.ADVISER, by=adviser.email, household_id=sharma.id, kind="mode", mode="fixed", effective_on=today - timedelta(days=10),
                                      reason="Client asked to go back to a fixed fee", document="The client's letter, countersigned")
            window = fee_cap.markets.tax_year(fee_cap.markets.for_firm(firm), today)
            terms = await adviser_fees.family_terms(s, firm, [sharma.id], window["start"], window["end"])
            check("a switch recorded this financial year is found, with the earlier ones kept", (terms["switched_on"], len(terms["switches"]), terms["mode"].mode),
                  (today - timedelta(days=10), 2, "fixed"))
            pv = await billing_core.preview(s, firm.id)
            sh = [e for e in pv["arrangements"] if e["household_name"] == "The Sharma Family" and e.get("fee_cap")]
            check("the preview now reads the cap as after a switch of mode", sh[0]["fee_cap"]["basis"].startswith("after a switch of mode"), True)
            check("a bill worked in a mode other than the election is flagged to confirm, never blocked for it",
                  [(c["code"], c["severity"]) for e in sh for c in e["checks"] if c["code"] == "fee_mode_differs"] in ([], [("fee_mode_differs", "warn")] * len(sh)), True)

            print("\n=== B-292: the second-opinion fee ===")
            await refused("a rate over 2.5% is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="second_opinion", assets=4_000_000, rate=0.03, fee=100_000, document="Invoice, filed"), "2.5%")
            await refused("a fee over the rate on those assets is refused", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="second_opinion", assets=4_000_000, rate=0.01, fee=50_000, document="Invoice, filed"), "more than the rate")
            await refused("no assets, no fee", adviser_fees.prepare(s, firm, UserRole.ADVISER, household_id=sharma.id, kind="second_opinion", assets=0, rate=0.01, fee=0, document="Invoice, filed"), "more than nothing")
            menon = next(h for n, h in homes.items() if "Menon" in n)
            menon_terms = await adviser_fees.family_terms(s, firm, [menon.id], window["start"], window["end"])
            check("the seed's second-opinion fee is on file and counts for its year", (len(menon_terms["second_opinion"]), menon_terms["second_opinion_fee"]), (1, 40_000.0))
            before = (await billing_core.preview(s, firm.id))["arrangements"]
            sh0 = [e for e in before if e["household_name"] == "The Sharma Family" and e.get("fee_cap")]
            check("a family with no second-opinion fee carries none", sh0[0]["fee_cap"]["second_opinion_fee"], 0.0)
            await adviser_fees.record(s, firm, UserRole.ADVISER, by=adviser.email, household_id=sharma.id, kind="second_opinion", assets=1_000_000_000, rate=0.025, fee=25_000_000,
                                      effective_on=today - timedelta(days=3), document="Engagement letter and invoice, filed")
            after = [e for e in (await billing_core.preview(s, firm.id))["arrangements"] if e["household_name"] == "The Sharma Family" and e.get("fee_cap")]
            check("a large second-opinion fee counts in what the family is charged, so the cap holds the bill",
                  (after[0]["fee_cap"]["second_opinion_fee"], after[0]["fee_cap"]["over"], any(c["code"] == "sebi_fee_cap" for c in after[0]["checks"])), (25_000_000.0, True, True))
            check("the managed AUA is unchanged by those assets", after[0]["fee_cap"]["aua"], sh0[0]["fee_cap"]["aua"])

            print("\n=== B-292: the deposit on the registration record ===")
            dep = adviser_fees.deposit(firm, today)
            check("the seed's deposit reads held in a liquid fund, which the circular allows since August 2025",
                  (dep["recorded"], dep["held_in"], dep["in_liquid_or_overnight"], dep["figure"]["key"]), (True, "liquid fund", True, "in_ia_deposit_liquid_funds"))
            check("a firm with none recorded says so and does not say none is held", ("does not mean none is held" in adviser_fees.deposit(Firm(jurisdiction="IN", settings={}), today)["said"]), True)
            await refused("a deposit held in crypto is not one of the places", _async(compliance_calendar.prepare_officers, firm, {"iaasb_deposit_held_in": "crypto"}, "Correcting the record"), "one of")
            kept = firm.settings
            firm.settings = {**kept, "sebi_registration": {k: v for k, v in kept["sebi_registration"].items() if not k.startswith("iaasb_")}}
            try:
                await refused("a deposit with no evidence is refused", _async(compliance_calendar.prepare_officers, firm, {"iaasb_deposit": "Rs 6,00,000"}, "Topping up"), "evidence")
            finally:
                firm.settings = kept
            p = compliance_calendar.prepare_officers(firm, {"iaasb_deposit_held_in": "Overnight Fund"}, "Moved the deposit to an overnight fund")
            check("the place is read case-blind", p["details"]["iaasb_deposit_held_in"], "overnight fund")
            check("the registration record lists the deposit's three lines", {"iaasb_deposit", "iaasb_deposit_held_in", "iaasb_deposit_document"} <= compliance_calendar.OFFICER_KEYS, True)

            print("\n=== B-300: the rate card's list ===")
            listed = dict(rate_card.listed_keys(today))
            check("a Union Budget figure is on it", listed.get("in_cg_equity_lt_rate"), "budget")
            check("so are the slabs, the surcharge, the cess and the cost inflation index", all(listed.get(k) == "budget" for k in ("in_new_regime_bands", "in_surcharge_new", "in_cess", "in_cii_2026_27")), True)
            check("a SEBI circular's figure is on it", (listed.get("in_ia_fee_fixed_cap"), listed.get("in_sif_min_ticket"), listed.get("in_pms_opex_cap")), ("sebi_circular",) * 3)
            check("a figure from a statute, a date or a proposal is not", [k for k in ("in_majority_age", "in_res_days_year", "in_dpdp_duties_start", "in_pis_individual_limit", "in_probate_not_compulsory") if k in listed], [])
            card = await rate_card.overview(s, firm)
            check("each row carries its source, its day and counsel's state", all({"source", "as_of", "kind_label", "counsel"} <= set(r) for r in card["figures"]), True)
            check("the counts add up", card["counts"]["total"], card["counts"]["budget"] + card["counts"]["sebi_circular"])
            check("the review year is the next February when it is November", (card["year"], card["in_february"], card["next_task_on"]), (today.year + 1, False, f"{today.year + 1}-02-01"))
            check("no task yet, and the page says nothing is a finding", (card["task"]["raised"], "Nothing here says" in card["note"]), (False, True))

            print("\n=== B-300: awaiting the Budget ===")
            row = next(r for r in card["figures"] if r["key"] == "in_cg_equity_lt_rate")
            check("the seed's mark reads beside the figure in place of counsel's state, keeping it", (row["counsel"]["state"], row["counsel"]["reviewed_state"], row["counsel"]["label"]),
                  ("awaiting_budget", "not_reviewed", "Awaiting the Budget"))
            check("the other figures are untouched", {r["counsel"]["state"] for r in card["figures"] if r["key"] != "in_cg_equity_lt_rate"} <= {"not_reviewed", "confirmed", "not_confirmed", "corrected", "changed_since"}, True)
            check("the counts carry the wait", card["counts"]["awaiting_budget"], 1)
            value_before = law_figures.value("in_cg_equity_lt_rate", today)
            comp = users[UserRole.COMPLIANCE]
            for role in (UserRole.ADVISER, UserRole.BRANCH_LEADER, UserRole.ADMIN, UserRole.OPERATIONS):
                await refused(f"{role.value} does not mark a figure: it is compliance's", counsel_review.prepare_watch(s, firm, role, figure_key="in_cg_equity_st_rate", reason="Budget awaited"), "compliance")
            await refused("a figure that is not Indian is refused", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="us_basic_exclusion", reason="Budget awaited"), "not an Indian")
            await refused("a SEBI circular's figure cannot await the Budget", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="in_ia_fee_fixed_cap", reason="Budget awaited"), "SEBI circular")
            await refused("a mark needs its reason", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="in_cg_equity_st_rate", reason=""), "why")
            await refused("a figure already marked is refused", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="in_cg_equity_lt_rate", reason="Budget awaited"), "already marked")
            await refused("a release of a figure not marked is refused", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="in_cg_equity_st_rate", state="released", reason="Budget has passed"), "not marked")
            await refused("an unknown state is refused", counsel_review.prepare_watch(s, firm, UserRole.COMPLIANCE, figure_key="in_cg_equity_st_rate", state="maybe", reason="Budget awaited"), "awaiting_budget")
            n = (await s.execute(select(func.count()).select_from(FigureWatch))).scalar_one()
            await counsel_review.record_watch(s, firm, UserRole.COMPLIANCE, by=comp.email, figure_key="in_cg_equity_st_rate", reason="The Budget may move the short-term rate")
            check("a mark is added, not edited", (await s.execute(select(func.count()).select_from(FigureWatch))).scalar_one(), n + 1)
            await counsel_review.record_watch(s, firm, UserRole.COMPLIANCE, by=comp.email, figure_key="in_cg_equity_st_rate", state="released", reason="The Budget kept the rate")
            check("a release ends the wait and the mark stays on file", ((await rate_card.overview(s, firm))["counts"]["awaiting_budget"], (await s.execute(select(func.count()).select_from(FigureWatch))).scalar_one()), (1, n + 2))
            check("the figure's value is never changed by any of this", law_figures.value("in_cg_equity_lt_rate", today), value_before)
            old = FigureWatch(firm_id=firm.id, figure_key="in_cg_equity_lt_rate", state="awaiting_budget", marked_on=date(2025, 1, 5), figure_as_of=date(2025, 1, 1), reason="Test, an older mark")
            ended = counsel_review.status_of("in_cg_equity_lt_rate", None, today, old)
            check("a mark ends by itself when a newer value is recorded for the figure", (ended["state"], ended["watch"]["state"]), ("not_reviewed", "value_recorded"))
            check("counsel's own states are all still there", {"awaiting_budget", "not_reviewed", "confirmed", "changed_since"} <= set(counsel_review.STATES), True)

            print("\n=== B-300: the February task, once a year ===")
            check("not February: nothing is raised", (await rate_card.raise_task(s, firm))["raised"], False)
            tasks_before = (await s.execute(select(func.count()).select_from(Task).where(Task.source == "rate_card"))).scalar_one()
            check("none yet", tasks_before, 0)
            feb = date(today.year + 1, 2, 10)
            first = await rate_card.raise_task(s, firm, today=feb)
            check("in February one task is raised, for compliance", (first["raised"], first["assigned"], first["figures"] == (await rate_card.overview(s, firm))["counts"]["total"]), (True, True, True))
            task = (await s.execute(select(Task).where(Task.source == "rate_card"))).scalars().one()
            check("it is assigned to the firm's compliance officer and due by the end of February",
                  (task.assigned_to == comp.id, task.due_date, task.subject_label, task.status), (True, date(feb.year, 2, 28), f"Rate card {feb.year}", "open"))
            check("it lists the figures with their kind, their day and counsel's state", all(k in task.detail for k in ("in_cg_equity_lt_rate", "in_sif_min_ticket", "Union Budget", "SEBI circular", "Not reviewed")), True)
            check("and says it is no finding", "Nothing here says a figure is out of date" in task.detail, True)
            again = await rate_card.raise_task(s, firm, today=feb + timedelta(days=9))
            check("run again, any day that February, it raises nothing", (again["raised"], (await s.execute(select(func.count()).select_from(Task).where(Task.source == "rate_card"))).scalar_one()), (False, 1))
            check("the overview now says the year's task exists", (await rate_card.overview(s, firm))["task"]["raised"], True)
            nxt = await rate_card.raise_task(s, firm, today=date(feb.year + 1, 2, 3))
            leap = (await s.execute(select(Task).where(Task.subject_label == f"Rate card {feb.year + 1}"))).scalar_one()
            check("the next year's February raises the next year's, once, due on its last day (2028 is a leap year)", (nxt["raised"], leap.due_date), (True, date(2028, 2, 29)))
            check("a firm outside India raises none", (await rate_card.raise_task(s, Firm(jurisdiction="NZ", settings={}), today=feb))["raised"], False)
            check("the task changes no figure", law_figures.value("in_cg_equity_lt_rate", today), value_before)
            from app.atlas import worker

            check("the worker schedules the check", callable(worker._run_rate_card), True)

            print("\n=== The tools and their cards ===")
            for key in ("record_accredited_investor", "record_adviser_fee_term", "record_figure_watch"):
                check(f"{key} stops for a Confirm card", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            for key in ("read_ticket_gates", "read_accredited_investors", "read_adviser_fee_terms", "read_rate_card"):
                check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
            tool = ToolExecutors(s, comp.id, UserRole.COMPLIANCE, firm.id)
            adv = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            card = await adv.preview_change("record_accredited_investor", {"person_id": str(people["Aarav Sharma"].id), "certificate_ref": "ACC/DEMO/TEST/9", "agency": "A demonstration agency (invented)",
                                                                            "valid_from": (today - timedelta(days=3)).isoformat(), "criteria": "Annual income of Rs 2 crore; or net worth",
                                                                            "document": "Accreditation certificate, filed"})
            labels = [r["label"] for r in card["rows"]]
            check("the certificate card names the person, the criteria stated, what it replaces and its effects", ("Person" in labels, "Criteria stated" in labels, "Effects" in labels, "Replaces" in labels), (True,) * 4)
            n = (await s.execute(select(func.count()).select_from(AccreditedInvestor))).scalar_one()
            check("a card writes nothing by being shown", (await s.execute(select(func.count()).select_from(AccreditedInvestor))).scalar_one(), n)
            done = await adv.execute("record_accredited_investor", {"person_id": str(people["Aarav Sharma"].id), "certificate_ref": "ACC/DEMO/TEST/9", "agency": "A demonstration agency (invented)",
                                                                     "valid_from": (today - timedelta(days=3)).isoformat(), "criteria": "Annual income of Rs 2 crore; or net worth",
                                                                     "document": "Accreditation certificate, filed"})
            check("confirmed, it is recorded, and the criteria are split into lines", (done["message"].startswith("Recorded"), [p for p in done["people"] if p["person"] == "Aarav Sharma"][0]["criteria"]), (True, ["Annual income of Rs 2 crore", "or net worth"]))
            card = await adv.preview_change("record_adviser_fee_term", {"household_id": str(homes["The Iyer Family"].id), "kind": "advance", "advance_months": 3, "document": "Signed agreement, filed"})
            check("the fee-term card names the household, the term and says it lowers no fee", ([r["label"] for r in card["rows"]][:3], any("lowers no fee" in r["value"] for r in card["rows"])), (["Household", "Term", "From"], True))
            await refused("a card for an advance of 20 months is refused before it is shown", adv.preview_change("record_adviser_fee_term", {
                "household_id": str(homes["The Iyer Family"].id), "kind": "advance", "advance_months": 20, "document": "Signed agreement, filed"}), "at most 12")
            done = await adv.execute("record_adviser_fee_term", {"household_id": str(homes["The Iyer Family"].id), "kind": "advance", "advance_months": 3, "document": "Signed agreement, filed"})
            check("confirmed, the household's terms carry it", (done["advance"]["advance_months"], done["applies"]), (3, True))
            card = await tool.preview_change("record_figure_watch", {"figure_key": "in_cg_equity_st_rate", "reason": "The next Budget may move it"})
            check("the watch card names the figure, its source and says the figure is unchanged", (card["rows"][0]["label"], any("unchanged" in r["value"] for r in card["rows"])), ("Figure", True))
            await refused("an adviser's watch card is refused: it is compliance's", adv.preview_change("record_figure_watch", {"figure_key": "in_cg_equity_st_rate", "reason": "The next Budget"}), "compliance")
            read = await tool.execute("read_rate_card", {})
            check("compliance reads the rate card", (read["counts"]["total"] > 20, "Nothing here says" in read["message"]), (True, True))
            await refused("an adviser does not read the rate card", adv.execute("read_rate_card", {}), "compliance")
            done = await tool.execute("record_figure_watch", {"figure_key": "in_cg_equity_st_rate", "reason": "The next Budget may move it"})
            check("confirmed, the mark is on the rate card", ([r["counsel"]["state"] for r in done["figures"] if r["key"] == "in_cg_equity_st_rate"], done["counts"]["awaiting_budget"]), (["awaiting_budget"], 2))
            read = await adv.execute("read_ticket_gates", {"household_id": str(sharma.id)})
            check("the gates tool answers for a household, with each figure carrying counsel's state", (len(read["categories"]), all(f["counsel"]["state"] == "not_reviewed" for f in read["figures"])), (5, True))
            read = await adv.execute("read_accredited_investors", {"household_id": str(sharma.id)})
            check("the accredited tool answers for a household", (read["applies"], "Confirm the exact criteria" in read["message"]), (True, True))
            read = await adv.execute("read_adviser_fee_terms", {"household_id": str(sharma.id)})
            check("the fee-terms tool answers for a household with its deposit and the not-compliance line", (read["deposit"]["recorded"], "none of this says the adviser complies" in read["message"]), (True, True))
            card = await ToolExecutors(s, users[UserRole.ADMIN].id, UserRole.ADMIN, firm.id).preview_change("record_sebi_registration", {
                "iaasb_deposit_held_in": "overnight fund", "reason": "Moved the deposit"})
            check("the registration card shows the deposit's place", any("Deposit held in" in r["label"] for r in card["rows"]), True)
            await refused("the registration card refuses a deposit in an unlisted place", ToolExecutors(s, users[UserRole.ADMIN].id, UserRole.ADMIN, firm.id).preview_change(
                "record_sebi_registration", {"iaasb_deposit_held_in": "crypto", "reason": "Moved the deposit"}), "one of")

            print("\n=== The seed, a second time ===")
            check("the L100 seed adds nothing a second time", await india_l100a.add(s, firm), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def _async(fn, *a, **k):
    return fn(*a, **k)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
