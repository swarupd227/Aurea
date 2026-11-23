"""Giving, laid out as figures (B-192).

    python -m tests.verify_giving

The law's figures and the arithmetic on households built by hand, then the demo firm from seed/run.py in a throwaway
SQLite file:
  - an IRA owner may give from the IRA from the day they reach 70 and a half, up to the year's limit (the IRS notices'
    figures), and it can satisfy this year's RMD;
  - stock held over a year in an account the household owns, largest gain first; an IRA's lots are not offered, one held
    a year or less is counted, and the deduction cap is 30% of the return's AGI;
  - a donor-advised fund bunching year against giving each year, with the 0.5% floor, the non-itemizer deduction a fund
    does not get and the 35% cap; not assessed without the yearly giving or a return;
  - a household outside the US is not assessed, it changes nothing, the tax page carries it and the conversation reads it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_give_"), "give.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import giving, law_figures, tax_intelligence, tax_returns  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
from app.models.advice_file import AdviceRecommendation  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Household, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []
TODAY = date(2026, 11, 22)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def person(pid, dob, ira=0.0):
    return {"id": pid, "full_name": pid.upper(), "date_of_birth": dob, "profile": {"tax": {"ira_value": ira}}}


def by_hand() -> None:
    print("\n=== The law ===")
    check("2026's QCD limit and split-interest amount (Notice 2025-67)",
          (law_figures.value("us_qcd_limit", TODAY), law_figures.value("us_qcd_split_interest", TODAY)), (111_000, 55_000))
    check("2027's (Notice 2026-66)",
          (law_figures.value("us_qcd_limit", date(2027, 7, 1)), law_figures.value("us_qcd_split_interest", date(2027, 7, 1))), (114_000, 57_000))
    check("the non-itemizer deduction and the floor, from 2026",
          (law_figures.value("us_nonitemizer_charity_mfj"), law_figures.value("us_nonitemizer_charity_single"), law_figures.value("us_charity_floor")),
          (2_000, 1_000, 0.005))

    print("\n=== From an IRA ===")
    check("70 and a half, to the day", giving._half_year_on(date(1955, 8, 15), 70.5), date(2026, 2, 15))
    check("a month without the day ends at its last", giving._half_year_on(date(1956, 3, 31), 70.5), date(2026, 9, 30))
    out = giving.qcd({"persons": [person("a", "1955-08-15", 1_000_000), person("b", "1952-06-01", 1_000_000),
                                  person("c", "1960-01-01", 500_000), person("d", "1950-01-01")]}, today=TODAY)
    rows = {r["person"]: r for r in out["people"]}
    check("only IRA owners are laid out", sorted(rows), ["A", "B", "C"])
    check("A is past 70 and a half, before RMDs: eligible, no RMD", (rows["A"]["eligible_now"], rows["A"]["rmd_this_year"]), (True, None))
    check("B, 74, gives up to the limit and could satisfy the RMD", (rows["B"]["limit"], rows["B"]["satisfies_rmd_up_to"]),
          (111_000, rows["B"]["rmd_this_year"]))
    check("B's RMD is the law's divisor at 74 on the profile's value", rows["B"]["rmd_this_year"], round(1_000_000 / 25.5, 2))
    check("C is not yet eligible, with the day", (rows["C"]["eligible_now"], rows["C"]["eligible_from"]), (False, "2030-07-01"))
    check("next year's limit, where the law has set it", rows["A"]["next_year_limit"], 114_000)

    print("\n=== Stock ===")
    lot = lambda acc, price, days, sym: {"account_id": acc, "account_name": acc, "symbol": sym, "instrument_name": sym,  # noqa: E731
                                         "quantity": 100.0, "cost_per_unit": 50.0, "current_price": price, "holding_days": days}
    lots = [lot("own", 150.0, 900, "BIG"), lot("own", 80.0, 800, "MID"), lot("own", 200.0, 100, "NEW"), lot("ira", 300.0, 900, "IRA"),
            lot("own", 40.0, 900, "LOSS")]
    st = giving.stock(lots, {"own": "individual", "ira": "traditional_ira"}, 300_000, today=TODAY)
    check("long-held gains in the household's own account, largest first", [l["symbol"] for l in st["lots"]], ["BIG", "MID"])
    check("one held a year or less is counted, not listed", st["short_held_with_gain"], 1)
    check("an IRA's lot is not offered", (st["not_own_accounts"], "IRA" in [l["symbol"] for l in st["lots"]]), (1, False))
    check("the deduction cap is 30% of AGI", st["deduction_cap"], 90_000.0)
    check("without a return the cap is not assessed", giving.stock(lots, {"own": "individual"}, None, today=TODAY)["deduction_cap"], None)

    print("\n=== Bunching into a donor-advised fund ===")
    ret = {"tax_year": 2025, "person_ids": ["a", "b"], "filing_status": "married_filing_jointly", "agi": 300_000,
           "marginal_rate": 0.24, "people": ["A", "B"], "document": "1040"}
    br = {"persons": [person("a", "1970-01-01"), person("b", "1971-01-01")], "tax_returns": [ret]}
    b = giving.bunching(br, today=TODAY, annual_giving=20_000, other_itemized=10_000)
    rows = {r["years"]: r for r in b["rows"]}
    check("giving each year: the standard deduction and the $2,000 non-itemizer deduction beat itemizing",
          (b["giving_each_year_itemizes"], rows[1]["deductions_each_year"]), (False, 34_200.0))
    check("three years in one: 10,000 + 60,000 less the 0.5% floor in the first year",
          rows[3]["first_year_deduction"], 10_000 + 60_000 - 1_500.0)
    check("and the standard deduction in the two after, with no non-itemizer deduction from the fund",
          rows[3]["deductions_bunched"], 68_500 + 2 * 32_200.0)
    check("the difference over three years", rows[3]["extra_deduction"], 132_900 - 3 * 34_200.0)
    check("worth the return's 24%", rows[3]["worth"], round(30_300 * 0.24, 2))
    top = dict(ret, marginal_rate=0.37)
    check("in the top bracket a deduction is worth 35%",
          giving.bunching({**br, "tax_returns": [top]}, today=TODAY, annual_giving=20_000, other_itemized=10_000)["deduction_worth_rate"], 0.35)
    check("no yearly giving said: not assessed", giving.bunching(br, today=TODAY, annual_giving=None, other_itemized=None)["status"], "not_assessed")
    check("no return: not assessed", giving.bunching({**br, "tax_returns": []}, today=TODAY, annual_giving=20_000, other_itemized=None)["status"],
          "not_assessed")
    big = giving.bunching(br, today=TODAY, annual_giving=60_000, other_itemized=0)
    check("five years past the 60% cash limit is flagged", {r["years"]: r["over_cash_limit"] for r in big["rows"]}[5], True)


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        us = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        nz = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Chen%")))).scalars().first()

        print("\n=== The demo firm ===")
        check("a household outside the US is not assessed", (await giving.for_household(s, nz.id, firm_id=firm.id))["status"], "not_assessed")
        before = (await s.execute(select(func.count(AdviceRecommendation.id)))).scalar()
        out = await giving.for_household(s, us.id, firm_id=firm.id)
        check("the Morrisons are laid out", out["status"], "assessed")
        check("their IRA owners are listed", len(out["qcd"]["people"]) >= 1, True)
        check("bunching waits for the yearly giving", out["bunching"]["status"], "not_assessed")
        people = (await s.execute(select(Person).where(Person.household_id == us.id))).scalars().all()
        await tax_returns.record(s, firm, us.id, by="Test", person_ids=[str(p.id) for p in people if not p.is_next_gen][:2], tax_year=2025,
                                 filing_status="married_filing_jointly", agi=1_020_000, taxable_income=900_000, marginal_rate=0.35,
                                 source_document="Form 1040 for 2025, test")
        await s.commit()
        out = await giving.for_household(s, us.id, firm_id=firm.id, annual_giving=50_000, other_itemized=40_000)
        check("with a return and the giving said, bunching is laid out", (out["bunching"]["status"], len(out["bunching"]["rows"])), ("assessed", 5))
        check("the stock cap reads the return's AGI", out["stock"]["deduction_cap"], round(0.30 * 1_020_000, 2))
        report = await tax_intelligence.for_household(s, us.id, firm.jurisdiction, firm_id=firm.id, window=True)
        check("the tax page carries giving", report["giving"]["status"], "assessed")
        scan = await tax_intelligence.for_household(s, us.id, firm.jurisdiction, firm_id=firm.id)
        check("a firm-wide scan does not", "giving" in scan, False)
        check("laying it out records nothing on the advice file", (await s.execute(select(func.count(AdviceRecommendation.id)))).scalar(), before)

        print("\n=== The conversation ===")
        check("it is a read", TOOLS["read_giving"].change_state, ToolChangeState.NO)
        check("no client holds it", UserRole.CLIENT in TOOLS["read_giving"].roles_required, False)
        ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
        read = await ex.read_giving({"household_id": str(us.id), "annual_giving": 50_000})
        check("Astra reads it", read["bunching"]["status"], "assessed")
        check("and says the choice is the client's and goes on the advice file",
              "what to give is the client's" in read["message"] and "advice file" in read["message"], True)
        try:
            await ex.read_giving({"household_id": str(us.id), "annual_giving": -5})
            check("a negative amount is refused", False, True)
        except Exception as exc:  # noqa: BLE001
            check("a negative amount is refused", "negative" in str(exc), True)


async def main() -> int:
    by_hand()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
