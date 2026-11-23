"""Healthcare in retirement, from what the firm states and what the law sets (B-191).

    python -m tests.verify_healthcare

On a household built by hand, then the demo firm from seed/run.py in a throwaway SQLite file:
  - the costs are two planning figures with no default, recorded by research or the portfolio team with a source;
    unset, healthcare is not assessed and a retirement plan is exactly what it was;
  - before 65 the firm's cost, from 65 the firm's cost beyond Part B plus Part B (the law's, by year) and the IRMAA
    surcharge from the income on file two years before, or a recorded return's AGI; RMD years' income is not on file,
    so IRMAA stops there and says so; one cost stated leaves the other phase not assessed;
  - the plan funds it on top of the income, a household outside the US is untouched, and the conversation reads it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_hc_"), "hc.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import assumptions, healthcare as hc, law_figures, retirement, tax_returns  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.decision_rights import ASSUMPTION_OWNERS  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
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


def brain(pre=None, med=None, *, ira=1_200_000.0, returns=(), **values) -> dict:
    return {"persons": [
        {"id": "a", "full_name": "Al", "date_of_birth": "1962-03-01", "profile": {"tax": {"ira_value": ira}}},
        {"id": "b", "full_name": "Bo", "date_of_birth": "1963-05-01", "profile": {"tax": {}}}],
        "household": {"values": values}, "tax_returns": list(returns),
        "planning": {"healthcare": {"pre65": pre, "medicare": med}, "inflation": {"cpi": 0.025, "healthcare": 0.05},
                     "inflation_assumed": []}}


RICH = [{"person_id": "a", "kind": "employer_pension", "name": "Pension", "planned_annual": 300_000, "inflation_linked": True, "start_age": 60}]


def by_hand() -> None:
    print("\n=== The law and the figures ===")
    check("Part B's standard premium, 2026 and 2027 (CMS)",
          (law_figures.value("us_part_b_premium", date(2026, 7, 1)), law_figures.value("us_part_b_premium", date(2027, 7, 1))), (202.90, 209.40))
    check("two healthcare figures in the assumption set",
          [k for k in assumptions.FIGURES if k.startswith("healthcare.")], ["healthcare.pre65", "healthcare.medicare"])
    check("each in money, with no default", {assumptions.FIGURES[k].unit for k in ("healthcare.pre65", "healthcare.medicare")}, {"money"})

    print("\n=== Not assessed until the firm states a cost ===")
    out = hc.lay_out(brain(), [], today=TODAY, from_year=2027, to_year=2040)
    check("nothing stated: not assessed, nothing to add", (out["status"], out["by_t"]), ("not_assessed", {}))

    print("\n=== Before 65 and on Medicare ===")
    out = hc.lay_out(brain(pre=15_000, med=4_000), [], today=TODAY, from_year=2027, to_year=2029)
    y27 = out["years"][0]
    al, bo = y27["people"]["Al"], y27["people"]["Bo"]
    check("Al is 65 in 2027: on Medicare", al["phase"], "medicare")
    check("Bo is 64: before Medicare, the stated cost grown a year at 5% and read in today's money",
          (bo["phase"], bo["amount_today"]), ("before_medicare", round(15_000 * 1.05 / 1.025, 2)))
    check("Al's Part B is 2027's law, a year of it", al["part_b"], round(12 * 209.40 / 1.025, 2))
    check("2027's premiums read 2025, a filed year with no return here: not assessed, and named",
          (al["irmaa"], al["irmaa_read"], any("IRMAA for 2027" in n for n in out["not_assessed"])), (None, "no_return_for_the_year", True))
    check("the year's total is the people's", abs(y27["total_today"] - (al["amount_today"] + bo["amount_today"])) <= 0.02, True)
    al28 = out["years"][1]["people"]["Al"]
    check("2028's read 2026's income on file: no income, tier 0", (al28["irmaa"], al28["irmaa_read"]), (0.0, "tier 0, from income on file"))
    check("the plan draws it in the year's own money", out["by_t"][1], y27["total"])
    y29 = out["years"][2]
    check("after 2027 Part B grows with healthcare inflation, named",
          (y29["people"]["Al"]["part_b_held"], any("Part B" in a for a in out["assumed"])), (True, True))

    print("\n=== IRMAA two years on ===")
    out = hc.lay_out(brain(pre=15_000, med=4_000), RICH, today=TODAY, from_year=2028, to_year=2030)
    al = out["years"][0]["people"]["Al"]
    check("$300,000 of income on file puts 2028's premiums in a surcharge tier", al["irmaa"] > 0, True)
    check("read from 2026's income on file", "income on file" in al["irmaa_read"], True)
    ret = [{"tax_year": 2025, "person_ids": ["a", "b"], "filing_status": "married_filing_jointly", "agi": 500_000, "people": ["Al", "Bo"], "document": "1040"}]
    out = hc.lay_out(brain(pre=15_000, med=4_000, returns=ret), [], today=TODAY, from_year=2027, to_year=2027)
    al = out["years"][0]["people"]["Al"]
    check("2027's premiums read the 2025 return's AGI", (al["irmaa_read"], al["irmaa"] > 0), ("tier 4, from the 2025 return", True))
    check("at the law's 2027 amounts, as published", al["irmaa"], round(12 * (460.60 + 88.30) / 1.025, 2))
    out = hc.lay_out(brain(pre=15_000, med=4_000), [], today=TODAY, from_year=2037, to_year=2040)
    check("once it reads an RMD year's income it stops, and says so",
          (out["years"][-1]["people"]["Al"]["irmaa"], any("RMD" in n for n in out["not_assessed"])), (None, True))
    odd = hc.lay_out(brain(pre=15_000, med=4_000, filing_status="married_joint"), [], today=TODAY, from_year=2027, to_year=2027)
    check("a filing status the law does not know leaves IRMAA not assessed", (odd["status"], odd["years"][0]["people"]["Al"]["irmaa"]),
          ("partly_assessed", None))

    print("\n=== One cost stated ===")
    out = hc.lay_out(brain(pre=15_000), [], today=TODAY, from_year=2027, to_year=2027)
    check("only the pre-65 cost: Medicare is not assessed, and named",
          (out["status"], out["years"][0]["people"]["Al"].get("status"), any("on Medicare" in n for n in out["not_assessed"])),
          ("partly_assessed", "not_assessed", True))


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        owner = (await s.execute(select(User).where(User.firm_id == firm.id, User.role.in_(ASSUMPTION_OWNERS)))).scalars().first()
        us = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        nz = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Chen%")))).scalars().first()

        print("\n=== The demo firm, before any cost is stated ===")
        read = await assumptions.read(s, firm)
        pre = next(f for f in read["figures"] if f["key"] == "healthcare.pre65")
        check("the figure reads not set, and whose is none", (pre["value"], pre["whose"]), (None, "none"))
        plan_us = await retirement.for_household(s, us.id, firm_id=firm.id)
        plan_nz = await retirement.for_household(s, nz.id, firm_id=firm.id)
        check("the Morrisons' plan says healthcare is not assessed", plan_us["healthcare"]["status"], "not_assessed")

        print("\n=== Stated by the portfolio team ===")
        try:
            await assumptions.prepare(s, firm, adviser, changes=[{"figure": "healthcare.pre65", "value": 15_000, "source": "x"}], reason="test")
            check("an adviser cannot state it", False, True)
        except assumptions.NotOwner:
            check("an adviser cannot state it", True, True)
        try:
            await assumptions.prepare(s, firm, owner, changes=[{"figure": "healthcare.pre65", "value": 15_000}], reason="test")
            check("a cost needs its source", False, True)
        except assumptions.AssumptionError:
            check("a cost needs its source", True, True)
        got = await assumptions.record(s, firm, owner, reason="Healthcare costs from the 2026 survey", changes=[
            {"figure": "healthcare.pre65", "value": 15_000, "source": "Test survey of individual premiums, 2026"},
            {"figure": "healthcare.medicare", "value": 4_000, "source": "Test survey of Medigap and Part D, 2026"}])
        await s.commit()
        check("recorded as the firm's", next(f for f in got["figures"] if f["key"] == "healthcare.pre65")["whose"], "firm")
        check("and the refresh says it does not measure retirement", got["recorded"]["refresh"].get("retirement_not_measured"), True)

        after_us = await retirement.for_household(s, us.id, firm_id=firm.id)
        after_nz = await retirement.for_household(s, nz.id, firm_id=firm.id)
        check("the Morrisons' plan now funds healthcare", after_us["healthcare"]["status"] in ("assessed", "partly_assessed"), True)
        check("their filing status, married_joint, leaves IRMAA not assessed and named",
              any("IRMAA" in n for n in after_us["healthcare"]["not_assessed"]), True)
        check("the first retirement year draws more", after_us["income_at_retirement"] > plan_us["income_at_retirement"], True)
        check("and the likelihood is no higher", after_us["success_probability"] <= plan_us["success_probability"], True)
        check("a household outside the US is untouched",
              (after_nz["success_probability"], after_nz["income_at_retirement"], after_nz["healthcare"]["status"]),
              (plan_nz["success_probability"], plan_nz["income_at_retirement"], "not_assessed"))

        people = (await s.execute(select(Person).where(Person.household_id == us.id))).scalars().all()
        await tax_returns.record(s, firm, us.id, by="Test", person_ids=[str(p.id) for p in people if not p.is_next_gen][:2], tax_year=2025,
                                 filing_status="married_filing_jointly", agi=1_020_000, taxable_income=900_000,
                                 source_document="Form 1040 for 2025, test")
        await s.commit()
        with_return = await retirement.for_household(s, us.id, firm_id=firm.id)
        check("with the return recorded IRMAA is read", any("IRMAA: the filing" in n for n in with_return["healthcare"]["not_assessed"]), False)

        print("\n=== The conversation ===")
        check("it is a read", TOOLS["read_healthcare_costs"].change_state, ToolChangeState.NO)
        check("no client holds it", UserRole.CLIENT in TOOLS["read_healthcare_costs"].roles_required, False)
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_healthcare_costs({"household_id": str(us.id)})
        check("Astra reads the years", len(read["years"]) > 0, True)
        check("and says the cover is the client's", "What cover to buy is the client's" in read["message"], True)


async def main() -> int:
    by_hand()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
