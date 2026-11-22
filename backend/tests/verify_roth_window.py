"""The Roth window year by year, and the withdrawal order it implies (B-190).

    python -m tests.verify_roth_window

The law's figures and arithmetic first, on a household built by hand, then the demo firm from seed/run.py in a throwaway
SQLite file (the Morrisons are its US household):
  - each year's deductions, brackets and IRMAA tiers are the law's for that year, a later year holds the last in
    today's money and says so, and a figure fixed in the statute (Social Security's base amounts, the senior deduction)
    shrinks in today's money and ends where the law ends it;
  - Social Security is taxed by IRC §86, so a conversion while it is paid costs more than the bracket's rate;
  - the room to a bracket's top and to the next IRMAA line are the conversions that reach them exactly;
  - the window runs from retirement to the year before the first RMD of an IRA owner, and is not assessed, or closed,
    when it cannot be read;
  - the withdrawal order is each year's room, not the fixed rule, and the usual order says why when there is no window;
  - it changes nothing, the tax page reads it, a firm-wide scan does not run it, and the conversation reads it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_rw_"), "rw.db")
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
from app.aurea_core import financial_plan, law_figures, roth_window as rw, tax_intelligence, tax_returns  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.financial_plan import IncomeStream  # noqa: E402
from app.models.graph import Household, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.tax_return import TaxReturn  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []
TODAY = date(2026, 11, 22)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def couple(ira_a=1_200_000.0, dob_a="1962-03-01", dob_b="1963-05-01", **values) -> dict:
    return {"persons": [
        {"id": "a", "full_name": "Al", "date_of_birth": dob_a, "profile": {"tax": {"ira_value": ira_a, "roth_value": 50_000}}},
        {"id": "b", "full_name": "Bo", "date_of_birth": dob_b, "profile": {"tax": {}}}],
        "household": {"values": values}, "tax_returns": []}


STREAMS = [{"person_id": "a", "kind": "social_security", "name": "SS", "planned_annual": 40_000, "inflation_linked": True, "start_age": 67},
           {"person_id": "b", "kind": "employer_pension", "name": "Pension", "planned_annual": 30_000, "inflation_linked": False, "start_age": 64}]
PLAN = {"years_to_retirement": 1, "income_need": 120_000, "assumed": []}


def law() -> None:
    print("\n=== The law's figures, per year ===")
    check("2026's joint standard deduction (Rev. Proc. 2025-32)", law_figures.value("us_standard_deduction_mfj", date(2026, 7, 1)), 32_200)
    check("2027's (Rev. Proc. 2026-35)", law_figures.value("us_standard_deduction_mfj", date(2027, 7, 1)), 33_200)
    check("2027's addition at 65, married and unmarried",
          (law_figures.value("us_aged_addition_married", date(2027, 7, 1)), law_figures.value("us_aged_addition_unmarried", date(2027, 7, 1))),
          (1_700, 2_100))
    check("2027's first IRMAA line, joint (CMS, 17 Nov 2026)", law_figures.tiers("us_irmaa_mfj", date(2027, 7, 1))[0][0], 224_000.0)
    check("and its top tier's Part B and Part D amounts", law_figures.tiers("us_irmaa_mfj", date(2027, 7, 1))[-1][1:], (502.40, 96.40))
    check("the senior deduction ends with 2028", law_figures.get("us_senior_deduction").status(date(2029, 1, 1)), "expired")
    try:
        law_figures.value("us_irmaa_mfj", date(2027, 7, 1))
        check("a table of tiers is refused as a number", False, True)
    except ValueError:
        check("a table of tiers is refused as a number", True, True)

    print("\n=== One year's arithmetic, this year's money ===")
    y = rw.YearLaw(2026, "married_filing_jointly", TODAY, 0.025)
    check("Social Security under the base amount is not taxed", y.taxed_ss(0, 20_000), 0.0)
    check("between the base amounts, half the excess", y.taxed_ss(20_000, 30_000), 1_500.0)
    check("above the adjusted base, 85% of the excess plus the lesser half", y.taxed_ss(40_000, 30_000), 15_350.0)
    check("never more than 85% of the benefits", y.taxed_ss(500_000, 30_000), 25_500.0)
    w = y.work(200_000, 0, aged=2)
    check("the senior deduction falls 6% of AGI over $150,000, each", round(w["senior"]), 6_000)
    check("a deduction for each 65 or over beside the standard", round(w["deduction"]), 32_200 + 2 * 1_650 + 6_000)
    late = rw.YearLaw(2029, "married_filing_jointly", TODAY, 0.025)
    check("in 2029 the senior deduction is gone", late.senior_each, 0.0)
    check("and 2029's brackets are 2027's, held in today's money", (late.brackets_held, late.bracket_year, round(late.brackets[0][0])),
          (True, 2027, round(25_600 / 1.025)))
    check("Social Security's base amount, fixed in the statute, shrinks in today's money", round(late.ss_base), round(32_000 / 1.025 ** 3))
    i27 = rw.YearLaw(2027, "married_filing_jointly", TODAY, 0.0)
    check("an IRMAA line is at or below", (i27.tier_of(224_000), i27.tier_of(224_001)), (0, 1))
    check("except the last, where the top tier starts", (i27.tier_of(749_999), i27.tier_of(750_000)), (4, 5))


def window() -> None:
    print("\n=== The window, year by year ===")
    out = rw.lay_out(couple(), PLAN, STREAMS, today=TODAY, inflation=0.025, base={})
    yrs = out["years"]
    check("it is assessed", out["status"], "assessed")
    check("from retirement to the year before Al's first RMD at 75", (out["window"]["from_year"], out["window"]["to_year"]), (2027, 2036))
    check("one row a year", len(yrs), 10)
    check("the filing status is assumed from the two adults, and says so", out["filing_basis"], "assumed from the adults on file")
    check("2027's brackets are the law's, 2028's are held", (yrs[0]["brackets_held"], yrs[1]["brackets_held"]), (False, True))
    check("a later year names the held figures", any("held in today's money" in a for a in out["assumed"]), True)
    check("a level pension buys less each year", yrs[1]["other_income"] < yrs[0]["other_income"], True)
    paid = next(y for y in yrs if y["social_security"])
    check("Social Security starts at Al's claim age, 67", paid["year"], 2029)
    room = paid["room_in_bracket"]
    check("a conversion while it is paid costs more than the bracket's rate", room["effective_rate"] > paid["bracket"], True)
    law29 = rw.YearLaw(paid["year"], "married_filing_jointly", TODAY, 0.025)
    aged = sum(1 for a in paid["ages"].values() if a >= 65)
    reached = law29.work(paid["other_income"], paid["social_security"], aged, room["room"])["taxable"]
    check("the room is the conversion that reaches the bracket's top", abs(reached - room["top"]) <= 1, True)
    ir = paid["irmaa"]
    check("IRMAA reads the premium year two on", ir["premium_year"], paid["year"] + 2)
    crossed = law29.work(paid["other_income"], paid["social_security"], aged, ir["room"])["agi"]
    check("its room is the conversion that reaches the next line", abs(crossed - ir["next_line"]) <= 1, True)
    check("the draw inside the room is the lesser of the draw and the room",
          paid["draw_within_room"], round(min(paid["draw"], room["room"]), 2))
    check("the room adds up", out["totals"]["room_in_brackets"], round(sum(y["room_in_bracket"]["room"] for y in yrs if y["room_in_bracket"])))
    check("it recommends nothing", rw.DECIDES.startswith("nothing"), True)

    print("\n=== A conversion that would raise Medicare premiums ===")
    rich = [{"person_id": "a", "kind": "employer_pension", "name": "Pension", "planned_annual": 200_000, "inflation_linked": True, "start_age": 60}]
    out = rw.lay_out(couple(), PLAN, rich, today=TODAY, inflation=0.0, base={})
    y = out["years"][0]
    check("with $200,000 a year, the bracket room passes the IRMAA line", y["bracket_room_crosses_irmaa"], True)
    check("and it is counted", out["totals"]["years_crossing_irmaa"] >= 1, True)

    print("\n=== Not assessed, or closed ===")
    no_dob = couple()
    no_dob["persons"][1]["date_of_birth"] = None
    check("no date of birth", rw.lay_out(no_dob, PLAN, STREAMS, today=TODAY, inflation=0.025, base={})["status"], "not_assessed")
    check("no traditional IRA", rw.lay_out(couple(ira_a=0), PLAN, STREAMS, today=TODAY, inflation=0.025, base={})["status"], "not_assessed")
    odd = rw.lay_out(couple(filing_status="married_joint"), PLAN, STREAMS, today=TODAY, inflation=0.025, base={})
    check("a filing status the law does not know", (odd["status"], "married_joint" in odd["reason"]), ("not_assessed", True))
    closed = rw.lay_out(couple(dob_a="1950-01-01", dob_b="1951-01-01"), PLAN, STREAMS, today=TODAY, inflation=0.025, base={})
    check("RMDs already started", closed["status"], "closed")
    filed = couple()
    filed["tax_returns"] = [{"tax_year": 2025, "person_ids": ["a"], "filing_status": "single", "people": ["Al"], "document": "1040"}]
    check("a joint couple's return read as filed", rw.lay_out(filed, PLAN, STREAMS, today=TODAY, inflation=0.025, base={})["filing_basis"],
          "the 2025 return")

    print("\n=== The withdrawal order ===")
    out = rw.lay_out(couple(), PLAN, STREAMS, today=TODAY, inflation=0.025, base={"decides": rw.DECIDES})
    seq = tax_intelligence._us_withdrawal_seq(couple(), out)
    check("with a window it is each year's room", (seq["basis"], len(seq["by_year"])), ("room_each_year", len(out["years"])))
    check("the traditional IRA is drawn inside the room", seq["sequences"][0]["priority_label"], "traditional · inside each year's room")
    check("the fixed rule is gone", "US optimal" in seq["guidance"], False)
    usual = tax_intelligence._us_withdrawal_seq(couple(ira_a=0), rw.lay_out(couple(ira_a=0), PLAN, STREAMS, today=TODAY, inflation=0.025, base={}))
    check("without one the usual order stands and says why", (usual["basis"], bool(usual["not_by_year"])), ("usual_order", True))


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
        people = (await s.execute(select(Person).where(Person.household_id == us.id))).scalars().all()
        robert = next(p for p in people if p.full_name.startswith("Robert"))
        patricia = next(p for p in people if p.full_name.startswith("Patricia"))

        print("\n=== The demo household ===")
        out = await rw.for_household(s, nz.id, firm_id=firm.id)
        check("a household outside the US is not assessed", out["status"], "not_assessed")
        out = await rw.for_household(s, us.id, firm_id=firm.id)
        check("the Morrisons' profile status, married_joint, is not one the law knows", out["status"], "not_assessed")

        await tax_returns.record(s, firm, us.id, by="Test", person_ids=[str(robert.id), str(patricia.id)], tax_year=2025,
                                 filing_status="married_filing_jointly", agi=1_020_000, taxable_income=900_000,
                                 source_document="Form 1040 for 2025, test")
        for p, amount, age in ((robert, 48_000, 70), (patricia, 14_400, 67)):
            await financial_plan.record_stream(s, p.id, firm_id=firm.id, by="Test", kind="social_security", name="Social Security",
                                               annual_amount=amount, start_age=age, source_document="SSA statement, test")
        await s.commit()
        before = ((await s.execute(select(func.count(IncomeStream.id)))).scalar(), (await s.execute(select(func.count(TaxReturn.id)))).scalar())
        out = await rw.for_household(s, us.id, firm_id=firm.id)
        check("with the return recorded it is read", (out["status"], out["filing_basis"]), ("assessed", "the 2025 return"))
        check("every year names the premium year two on", all(y["irmaa"]["premium_year"] == y["year"] + 2 for y in out["years"]), True)
        check("it names the assumption version, none recorded here", ("assumptions_version" in out, out["assumptions_version"]), (True, None))

        report = await tax_intelligence.for_household(s, us.id, firm.jurisdiction, firm_id=firm.id, window=True)
        check("the tax page carries the window", report["roth_window"]["status"], "assessed")
        check("and its withdrawal order is each year's room", report["withdrawal_sequencing"]["basis"], "room_each_year")
        scan = await tax_intelligence.for_household(s, us.id, firm.jurisdiction, firm_id=firm.id)
        check("a firm-wide scan does not run it", scan["roth_window"], None)
        after = ((await s.execute(select(func.count(IncomeStream.id)))).scalar(), (await s.execute(select(func.count(TaxReturn.id)))).scalar())
        check("laying it out changes nothing", after, before)

        print("\n=== The conversation ===")
        check("it is a read", TOOLS["read_roth_window"].change_state, ToolChangeState.NO)
        check("no client holds it", UserRole.CLIENT in TOOLS["read_roth_window"].roles_required, False)
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_roth_window({"household_id": str(us.id)})
        check("Astra reads the years", len(read["years"]), len(out["years"]))
        check("and says the conversion is the client's", "whether to convert is the client's" in read["message"], True)


async def main() -> int:
    law()
    window()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
