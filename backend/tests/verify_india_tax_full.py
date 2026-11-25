"""Prove the rest of the Indian tax engine: classes, losses, grandfathering, regimes, advance tax and what a harvest flags.

Each figure is read from law_figures with its source; a loss is carried forward only from a return filed on time; a 2018 value
applies only where a person recorded it; the regime comparison does not pick where the old regime's deductions are not on
file; and no flag says a rebuy is safe. Runs against a throwaway SQLite database.

    python -m tests.verify_india_tax_full
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, law_figures, markets, tax_india as ti, tax_india_full as full, tax_intelligence
from app.core.db import Base
from app.models.enums import MarketType
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pure() -> None:
    print("\n=== Every figure is the law_figures one, with its source ===")
    check("the rates the engine uses are the held ones",
          (ti.RATE_ST_EQUITY, ti.RATE_LT, ti.LTCG_EXEMPTION, ti.CESS, ti.SURCHARGE_CAP), (0.20, 0.125, 125000, 0.04, 0.15))
    for key in ("in_cg_equity_lt_rate", "in_new_regime_bands", "in_advance_tax_15_jun", "in_cii_2026_27", "in_old_regime_std_deduction"):
        f = law_figures.get(key)
        check(f"{key} names its source and a date", bool(f.source) and f.as_of.year == 2026, True)
    check("a figure the research did not state says it is assumed",
          "Assumed" in law_figures.get("in_old_regime_std_deduction").source and "Assumed" in law_figures.get("in_advance_tax_floor").source, True)
    check("the secondary source is named for the index", "secondary" in law_figures.get("in_cii_2026_27").source, True)

    print("\n=== Asset classes and holding periods, by the day ===")
    rows = {c["key"]: c for c in full.asset_classes(date(2026, 11, 25))}
    check("twelve classes", len(rows), 12)
    check("listed equity: 12 months, 20% and 12.5% above the exemption",
          (rows["listed_equity"]["long_after_months"], rows["listed_equity"]["short_text"], rows["listed_equity"]["long_text"],
           rows["listed_equity"]["exemption"]), (12, "20%", "12.5% above the exemption", 125000))
    check("a specified fund is never long-term", (rows["specified_fund"]["long_after_months"], rows["specified_fund"]["short_text"]), (None, "Slab"))
    check("gold, unlisted and foreign shares and land need 24 months",
          [rows[k]["long_after_months"] for k in ("physical_gold", "unlisted", "foreign_shares", "land_building")], [24, 24, 24, 24])
    check("other long-term has no exemption", rows["other_listed"]["exemption"], None)
    bought = date(2024, 1, 1)
    check("foreign shares at 24 months exactly are short-term", ti.bucket("foreign_shares", bought, date(2026, 1, 1)), ti.ST_SLAB)
    check("a day more and they are long-term at 12.5%, no exemption", ti.bucket("foreign_shares", bought, date(2026, 1, 2)), ti.LT_OTHER)
    check("physical gold follows the 24 months", ti.bucket("physical_gold", bought, date(2025, 6, 1)), ti.ST_SLAB)
    check("a market-linked debenture is never long-term", ti.bucket("mld", date(2020, 1, 1), date(2026, 11, 1)), ti.ST_SLAB)
    check("nor is unlisted debt", ti.long_term_from("unlisted_debt", bought), None)

    print("\n=== Set-off: short-term against anything, long-term against long-term only ===")
    s = ti.set_off({ti.LT_EQUITY: 300000, ti.ST_EQUITY: -50000})
    check("a short-term loss reduces a long-term gain", s["net"][ti.LT_EQUITY], 250000.0)
    s = ti.set_off({ti.ST_EQUITY: 80000, ti.LT_EQUITY: -90000})
    check("a long-term loss never touches a short-term gain", (s["net"][ti.ST_EQUITY], s["carry_forward"]["long_term"]), (80000.0, 90000.0))
    s = ti.set_off({ti.LT_EQUITY: 200000}, {"long_term": 120000})
    check("a brought-forward long-term loss reduces the gain before the exemption", (s["net"][ti.LT_EQUITY], s["exempt_used"]), (80000.0, 80000.0))

    print("\n=== Grandfathering: the higher of cost and the lower of the 2018 value and the sale price ===")
    old = date(2016, 3, 1)
    check("a 2018 value above cost lifts the cost", ti.grandfathered_cost("listed_equity", old, 100.0, 400.0, 300.0), 300.0)
    check("capped at the sale value", ti.grandfathered_cost("listed_equity", old, 100.0, 250.0, 300.0), 250.0)
    check("never below the cost", ti.grandfathered_cost("listed_equity", old, 280.0, 400.0, 150.0), 280.0)
    check("with no value recorded the cost stands", ti.grandfathered_cost("listed_equity", old, 100.0, 400.0, None), 100.0)
    check("a lot bought on 1 Feb 2018 is not grandfathered", ti.grandfathered_cost("listed_equity", date(2018, 2, 1), 100.0, 400.0, 300.0), 100.0)
    check("and neither is a debt fund", ti.grandfathered_cost("other_listed", old, 100.0, 400.0, 300.0), 100.0)

    print("\n=== Real estate: the lower of 12.5% and 20% on the indexed gain, for those the law gives it ===")
    r = full.real_estate_choice(acquired_on=date(2015, 1, 1), sold_on=date(2026, 11, 1), cost=5_000_000, proceeds=10_000_000,
                                cii_acquired=240, cii_sold=384, resident_individual_or_huf=True)
    check("indexation wins here", (r["tax_without_indexation"], r["tax_with_indexation"], r["tax"], r["choice"]),
          (625000.0, 400000.0, 400000.0, "20% on the indexed gain"))
    r = full.real_estate_choice(acquired_on=date(2015, 1, 1), sold_on=date(2026, 11, 1), cost=5_000_000, proceeds=10_000_000,
                                cii_acquired=240, cii_sold=384, resident_individual_or_huf=False)
    check("a non-resident has the flat rate only", (r["tax"], r["tax_with_indexation"]), (625000.0, None))
    r = full.real_estate_choice(acquired_on=date(2025, 1, 1), sold_on=date(2026, 11, 1), cost=5_000_000, proceeds=10_000_000,
                                cii_acquired=None, cii_sold=None, resident_individual_or_huf=True)
    check("bought after 23 Jul 2024 there is no choice", r["tax"], 625000.0)
    r = full.real_estate_choice(acquired_on=date(2015, 1, 1), sold_on=date(2026, 11, 1), cost=5_000_000, proceeds=10_000_000,
                                cii_acquired=None, cii_sold=384, resident_individual_or_huf=True)
    check("with no index for the year of purchase it is not assessed", (r["needs"] != [], "Not assessed" in r["choice"]), (True, True))

    print("\n=== The new regime against the old ===")
    c = full.regime_comparison(income=1_500_000, salary=1_500_000, deductions=None, age=40, gains=0, is_individual=True)
    check("new regime tax, with cess", c["new"]["tax"], 97500.0)
    check("old regime with no deductions on file", c["old"]["tax"], 257400.0)
    check("with no deductions on file it does not pick", (c["conclusive"], c["old_deductions_recorded"]), (False, False))
    check("it says what deductions would make the old regime win", c["break_even_deductions"] is not None and c["break_even_deductions"] > 0, True)
    be = full.regime_tax("old", income=1_500_000, salary=1_500_000, deductions=c["break_even_deductions"], age=40, gains=0)["tax"]
    check("at that figure the two are level", abs(be - c["new"]["tax"]) < 5, True)
    check("and it asks for them", any("deductions" in n for n in c["needs"]), True)
    d = full.regime_comparison(income=1_500_000, salary=1_500_000, deductions=700_000, age=40, gains=0, is_individual=True)
    check("with deductions recorded it picks", (d["conclusive"], d["cheaper"]), (True, "old"))
    d = full.regime_comparison(income=1_500_000, salary=1_500_000, deductions=150_000, age=40, gains=0, is_individual=True)
    check("a small deduction leaves the new regime cheaper", (d["cheaper"], d["saving"] > 0), ("new", True))
    low = full.regime_tax("new", income=1_100_000, salary=1_100_000, deductions=0, age=40, gains=0)
    check("the rebate takes a Rs 11 lakh salary to nil", (low["slab_tax"], low["rebate"], low["tax"]), (42500.0, 42500.0, 0.0))
    over = full.regime_tax("new", income=1_100_000, salary=1_100_000, deductions=0, age=40, gains=300_000)
    check("gains count toward the rebate line", over["rebate"], 0.0)
    rich = full.regime_tax("new", income=30_000_000, salary=None, deductions=0, age=40, gains=0)
    check("surcharge by income: 25% above Rs 2 crore in the new regime", rich["surcharge_rate"], 0.25)
    rich_old = full.regime_tax("old", income=60_000_000, salary=None, deductions=0, age=40, gains=0)
    check("and 37% above Rs 5 crore in the old", rich_old["surcharge_rate"], 0.37)
    senior = full.regime_tax("old", income=500_000, salary=None, deductions=0, age=65, gains=0)
    check("a senior's old-regime exemption is Rs 3 lakh", senior["slab_tax"], 10000.0)
    check("a company has no comparison", full.regime_comparison(income=1, salary=None, deductions=None, age=None, gains=0, is_individual=False)["assessed"], False)
    check("no income recorded is not assessed", (lambda x: (x["assessed"], x["needs"]))(
        full.regime_comparison(income=None, salary=None, deductions=None, age=None, gains=0, is_individual=True)), (False, ["annual income"]))

    print("\n=== Advance tax and the dates ===")
    a = full.advance_tax(100_000, date(2026, 11, 25), needs=[])
    check("four instalments of 15, 45, 75 and 100 per cent", [i["amount"] for i in a["instalments"]], [15000.0, 30000.0, 30000.0, 25000.0])
    check("the dates", [i["due_on"] for i in a["instalments"]], ["2026-06-15", "2026-09-15", "2026-12-15", "2027-03-15"])
    check("a date that has passed says its payment is not recorded, not overdue",
          [(i["state"], i["paid"]) for i in a["instalments"][:2]], [("passed", "not recorded")] * 2)
    check("15 December is 20 days out", (a["instalments"][2]["days"], a["instalments"][2]["state"]), (20, "upcoming"))
    check("under the floor nothing is asked", (lambda x: (x["asked"], [i["amount"] for i in x["instalments"]]))(
        full.advance_tax(5_000, date(2026, 11, 25), needs=[])), (False, [0.0] * 4))
    check("with no tax known it is not assessed", full.advance_tax(None, date(2026, 11, 25), needs=["annual income"])["assessed"], False)
    dates = full.return_dates(date(2027, 2, 1))
    check("the return is due 31 July after the year, on an assumed basis", (dates[-1]["due_on"], "assumed" in dates[-1]["citation"]), ("2027-07-31", True))
    check("within 14 days it is due soon", full.return_dates(date(2026, 12, 5))[2]["state"], "due_soon")

    print("\n=== What a harvest flags, and that a rebuy is never called safe ===")
    flags = full.interaction_flags([{"kind": "loss", "symbol": "TCS.NS", "long_term": True}, {"kind": "exemption", "symbol": "INFY.NS"}],
                                   {ti.LT_EQUITY: 20000.0}, {"carry_forward": {"long_term": 0}})
    kinds = [f["kind"] for f in flags]
    check("a long-term loss against gains the exemption covers is flagged", "exemption_burn" in kinds, True)
    check("a rebuy is a review item", "rebuy_review" in kinds, True)
    check("a gain harvest raises the substance question", "gain_harvest_review" in kinds, True)
    check("no flag says it is safe", any(" safe" in f["text"].lower() and "nothing here says it is safe" not in f["text"].lower() for f in flags), False)
    check("and no flag speaks of a 30-day wash sale", any("wash" in f["text"].lower() and "no wash-sale rule" not in f["text"].lower() for f in flags), False)


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                        settings={"timezone": "Asia/Kolkata", "execution": {"lot_method": "HIFO", "fee_bps": 0, "fee_minimum": 0}})
            other = Firm(id=uuid.uuid4(), name="Astra US", slug="us", jurisdiction="US", regulator="SEC", base_currency="USD", settings={})
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            year = markets.tax_year(markets.MARKETS["IN"], today)

            def inst(sym, cls, tax_class, close):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class=cls, market_type=MarketType.PUBLIC,
                               currency="INR", values_tags={"tax_class": tax_class})
                s.add(i)
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=close, currency="INR", source="yahoo", is_real=True))
                return i

            itc, gold = inst("ITC.NS", "equity", "listed_equity", 400), inst("GOLDBEES.NS", "commodity", "other_listed", 100)
            await s.flush()
            family = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharma Family", values={})
            alien = Household(id=uuid.uuid4(), firm_id=other.id, name="Elsewhere", values={})
            s.add_all([family, alien])
            await s.flush()

            async def taxpayer(name, **tax):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=family.id, full_name=name,
                           date_of_birth=date(1980, 5, 1), profile={"tax": tax} if tax else {})
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate", mandate_type="advisory",
                            suitability={"capacity_for_loss": "high"}, constraints={})
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate=m, name=f"{name} demat", currency="INR", custodian="CDSL", cash_balance=0)
                s.add_all([m, a])
                await s.flush()
                return p, m, a

            rajesh, _, a_rajesh = await taxpayer("Rajesh Sharma", regime="new", marginal_rate=0.30, annual_income=6_000_000, salary_income=4_000_000)
            meera, _, a_meera = await taxpayer("Meera Sharma", regime="new", marginal_rate=0.20)
            stranger = Person(id=uuid.uuid4(), firm_id=other.id, household_id=alien.id, full_name="Not Ours", profile={})
            s.add(stranger)
            await s.flush()

            h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a_rajesh.id, instrument_id=itc.id, quantity=100,
                        market_value=40000, cost_basis=10000)
            s.add(h)
            await s.flush()
            s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=100, cost_per_unit=100.0, acquired_on=date(2016, 3, 1)))
            sold = year["start"] if year["start"] <= today else today
            s.add(Transaction(firm_id=firm.id, account_id=a_rajesh.id, instrument_id=itc.id, txn_type="sell", quantity=100, price=400,
                              amount=40000, trade_date=sold,
                              lineage={"realised_gain": 30000, "fees": 0, "lots": [{"acquired_on": "2016-03-01", "quantity": 100, "cost": 10000}]}))
            await s.flush()

            print("\n=== The report before anything is recorded ===")
            r = await tax_intelligence.for_household(s, family.id, "IN", firm_id=firm.id)
            raj = next(t for t in r["taxpayers"] if t["name"] == "Rajesh Sharma")
            mee = next(t for t in r["taxpayers"] if t["name"] == "Meera Sharma")
            check("a pre-2018 lot with no value is listed, as it was", raj["grandfathered"], [{"symbol": "ITC.NS", "account": "Rajesh Sharma demat", "quantity": 100.0}])
            check("the gain is on cost", raj["realised"][ti.LT_EQUITY], 30000.0)
            g = raj["engine"]["grandfathering"][0]
            check("grandfathering is not applied and says what it needs", (g["applied"], g["needs"] is not None), (False, True))
            check("the ledger is empty and not in use", (raj["engine"]["loss_ledger"], raj["engine"]["ledger_in_use"]), ([], False))
            check("the regime comparison is worked from the income recorded", (raj["engine"]["regime"]["assessed"], mee["engine"]["regime"]["assessed"]), (True, False))
            check("no income is not assessed for advance tax either", mee["engine"]["advance_tax"]["assessed"], False)
            check("Rajesh has an advance tax schedule", (raj["engine"]["advance_tax"]["assessed"], len(raj["engine"]["advance_tax"]["instalments"])), (True, 4))
            check("it is the gains' tax plus the income's",
                  raj["engine"]["advance_tax"]["year_tax"],
                  round(raj["estimated_tax"] + raj["engine"]["regime"]["new"]["tax"], 2))
            check("the engine says it is an estimate", "estimate for a qualified person" in raj["engine"]["estimate"], True)

            print("\n=== A 31 Jan 2018 value, recorded by a person ===")
            for label, kw in (("a debt fund", dict(symbol="GOLDBEES.NS", fmv_per_unit=90, document="NSE bhavcopy 31 Jan 2018")),
                              ("a price of nothing", dict(symbol="ITC.NS", fmv_per_unit=0, document="NSE bhavcopy 31 Jan 2018")),
                              ("no source", dict(symbol="ITC.NS", fmv_per_unit=300, document="")),
                              ("an unknown symbol", dict(symbol="NOPE.NS", fmv_per_unit=300, document="NSE bhavcopy 31 Jan 2018"))):
                try:
                    await full.prepare_fmv(s, firm, **kw)
                    check(f"{label} is refused", "accepted", "refused")
                except full.TaxFactError:
                    check(f"{label} is refused", "refused", "refused")
            try:
                await full.prepare_fmv(s, other, symbol="ITC.NS", fmv_per_unit=300, document="NSE bhavcopy")
                check("another market's firm is refused", "accepted", "refused")
            except full.TaxFactError:
                check("another market's firm is refused", "refused", "refused")
            await full.record_fmv(s, firm, by="ops@astra", symbol="ITC.NS", fmv_per_unit=300, document="NSE bhavcopy 31 Jan 2018")
            r = await tax_intelligence.for_household(s, family.id, "IN", firm_id=firm.id)
            raj = next(t for t in r["taxpayers"] if t["name"] == "Rajesh Sharma")
            check("the realised gain is now on the grandfathered cost", raj["realised"][ti.LT_EQUITY], 10000.0)
            g = raj["engine"]["grandfathering"][0]
            check("and the lot shows its cost before and after", (g["applied"], g["cost_before"], g["cost_after"]), (True, 10000.0, 30000.0))
            check("the list the report always carried is unchanged", raj["grandfathered"], [{"symbol": "ITC.NS", "account": "Rajesh Sharma demat", "quantity": 100.0}])

            print("\n=== Losses carried forward ===")
            for label, kw in (("this year's", dict(person_id=rajesh.id, tax_year=year["label"], kind="long_term", amount=1000, document="ITR-2 ack")),
                              ("a year past eight", dict(person_id=rajesh.id, tax_year="2010-11", kind="long_term", amount=1000, document="ITR-2 ack")),
                              ("a bad year", dict(person_id=rajesh.id, tax_year="2023", kind="long_term", amount=1000, document="ITR-2 ack")),
                              ("a bad kind", dict(person_id=rajesh.id, tax_year="2023-24", kind="medium", amount=1000, document="ITR-2 ack")),
                              ("more used than lost", dict(person_id=rajesh.id, tax_year="2023-24", kind="long_term", amount=1000, used=2000, document="ITR-2 ack")),
                              ("no source", dict(person_id=rajesh.id, tax_year="2023-24", kind="long_term", amount=1000, document="")),
                              ("nobody", dict(tax_year="2023-24", kind="long_term", amount=1000, document="ITR-2 ack")),
                              ("another firm's person", dict(person_id=stranger.id, tax_year="2023-24", kind="long_term", amount=1000, document="ITR-2 ack"))):
                try:
                    await full.prepare_loss(s, firm, **kw)
                    check(f"{label} is refused", "accepted", "refused")
                except full.TaxFactError:
                    check(f"{label} is refused", "refused", "refused")
            try:
                await full.prepare_loss(s, other, person_id=stranger.id, tax_year="2023-24", kind="long_term", amount=1000, document="ITR ack")
                check("a firm outside India is refused", "accepted", "refused")
            except full.TaxFactError:
                check("a firm outside India is refused", "refused", "refused")
            await full.record_loss(s, firm, by="adviser@astra", person_id=rajesh.id, tax_year="2023-24", kind="long_term", amount=200000,
                                   return_filed_on="2024-07-20", document="ITR-2 acknowledgement AY 2024-25")
            await full.record_loss(s, firm, by="adviser@astra", person_id=rajesh.id, tax_year="2022-23", kind="short_term", amount=50000,
                                   return_filed_on="2023-09-01", document="ITR-2 acknowledgement AY 2023-24, filed late")
            await full.record_loss(s, firm, by="adviser@astra", person_id=rajesh.id, tax_year="2021-22", kind="short_term", amount=40000,
                                   document="Working papers; no return found")
            await full.record_loss(s, firm, by="adviser@astra", person_id=rajesh.id, tax_year="2017-18", kind="long_term", amount=70000,
                                   return_filed_on="2018-07-10", document="ITR-2 acknowledgement AY 2018-19")
            rows = await full.ledger(s, firm.id, person_id=rajesh.id, today=today)
            by = {(x["tax_year"], x["kind"]): x for x in rows}
            check("on time, it carries", (by[("2023-24", "long_term")]["state"], by[("2023-24", "long_term")]["available"]), ("carried", 200000.0))
            check("it may be set off to the eighth year after", by[("2023-24", "long_term")]["last_year"], "2031-32")
            check("a return filed late carries nothing", (by[("2022-23", "short_term")]["state"], by[("2022-23", "short_term")]["available"]), ("late_return", 0.0))
            check("no return on file carries nothing", (by[("2021-22", "short_term")]["state"], by[("2021-22", "short_term")]["available"]), ("no_return", 0.0))
            check("after eight years it expires", by[("2017-18", "long_term")]["state"], "expired")
            bf = await full.brought_forward(s, firm.id, person_id=rajesh.id, today=today, fallback={"short_term": 999})
            check("the year starts with the ledger, replacing the profile's single figure", (bf["long_term"], bf["short_term"], bf["from_ledger"]), (200000.0, 0.0, True))
            bf = await full.brought_forward(s, firm.id, person_id=meera.id, today=today, fallback={"short_term": 999})
            check("a taxpayer with no ledger keeps the profile's", bf, {"short_term": 999})
            r = await tax_intelligence.for_household(s, family.id, "IN", firm_id=firm.id)
            raj = next(t for t in r["taxpayers"] if t["name"] == "Rajesh Sharma")
            mee = next(t for t in r["taxpayers"] if t["name"] == "Meera Sharma")
            check("the loss absorbs the long-term gain before the exemption", (raj["position"]["net"][ti.LT_EQUITY], raj["estimated_tax"]), (0.0, 0.0))
            check("what is left carries forward", raj["position"]["carry_forward"]["long_term"], 190000.0)
            check("the ledger is in use for him", raj["engine"]["ledger_in_use"], True)
            check("it never reaches his wife", (mee["engine"]["ledger_in_use"], mee["engine"]["loss_ledger"]), (False, []))
            check("available losses are summed by kind", raj["engine"]["loss_available"], {"short_term": 0.0, "long_term": 200000.0})

            await full.record_loss(s, firm, by="adviser@astra", person_id=rajesh.id, tax_year="2023-24", kind="long_term", amount=200000, used=60000,
                                   return_filed_on="2024-07-20", document="ITR-2 acknowledgement AY 2024-25; 60,000 set off in 2025-26")
            rows = await full.ledger(s, firm.id, person_id=rajesh.id, today=today)
            check("the latest record for a year and kind stands", [x["available"] for x in rows if x["tax_year"] == "2023-24"], [140000.0])
            rec = await full.records(s, firm)
            check("the records view lists the taxpayer's ledger and the 2018 value",
                  ([t["name"] for t in rec["taxpayers"]], [v["symbol"] for v in rec["grandfather_values"]]), (["Rajesh Sharma"], ["ITC.NS"]))
            check("and a firm outside India has none", (await full.records(s, other))["applies"], False)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
