"""One goal, one likelihood (B-131): every screen reads goal_tradeoff.likelihoods, which works from what is on file.

    python -m tests.verify_goal_likelihood

It checks that:

  - a goal's horizon is its date, else the horizon recorded on it, else assumed and named;
  - a goal with a share of the wealth set aside is funded from that share alone, the goals with none share
    the rest in priority order, and what a matured goal leaves joins the pool; shares over the whole are
    scaled and say so;
  - career earnings come only from a recorded income and date of birth, sit beside the goals and never fund
    them: a likelihood is the same with or without an income on file;
  - the Canvas, the goals page, the staff planning view and the agents give each goal the same figure;
  - the retirement engine names what it assumed and that it answers a different question;
  - the contribution lever is in the firm's currency, and no copy of the old per-goal engine is left.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import _signals
from app.api import core as core_api
from app.aurea_core import client_view, firm_time, goal_tradeoff, retirement
from app.aurea_core.graph import household_brain
from app.core.db import Base
from app.llm.executors import ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 3)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def brain(goals: list[dict], persons: list[dict] | None = None, total: float = 1_000_000.0) -> dict:
    return {"household": {"id": str(uuid.uuid4()), "name": "T"}, "currency": "NZD", "persons": persons or [],
            "goals": goals, "totals": {"total_value": total, "by_asset_class": {"equity": total * 0.6, "fixed_income": total * 0.4}}}


def goal(name: str, target: float, *, years=None, when=None, share=None, priority=None, contribution=None, kind="other") -> dict:
    a = {k: v for k, v in (("years", years), ("funding_share", share), ("annual_contribution", contribution)) if v is not None}
    return {"id": name, "name": name, "kind": kind, "target_amount": target, "priority": priority, "assumptions": a,
            "target_date": when.isoformat() if when else None}


def pure() -> None:
    print("\n=== A goal's horizon is what is on file ===")
    check("its date first", goal_tradeoff.horizon(goal("g", 1, years=3, when=TODAY + timedelta(days=round(365.25 * 8))), TODAY), (8, "target_date"))
    check("else the horizon recorded on it", goal_tradeoff.horizon(goal("g", 1, years=6), TODAY), (6, "recorded"))
    check("else assumed, and named", goal_tradeoff.horizon(goal("g", 1), TODAY), (goal_tradeoff.ASSUMED_YEARS, "assumed"))
    result = goal_tradeoff.likelihoods(brain([goal("Car", 50_000)]), TODAY)
    check("an assumed horizon is said on the goal", result["goals"][0]["assumed"], ["years"])
    check("and for the household", result["assumed"], ["years"])

    print("\n=== A share set aside funds its goal alone; the rest share the pool ===")
    shared_only = goal_tradeoff.likelihoods(brain([goal("Home", 700_000, years=5)]), TODAY)["goals"][0]
    earmarked = goal_tradeoff.likelihoods(brain([goal("Home", 700_000, years=5, share=0.3)]), TODAY)["goals"][0]
    check("a goal with 30% set aside is funded from that 30%", earmarked["success_probability"] < shared_only["success_probability"], True)
    check("and says so", (earmarked["share"], shared_only["share"]), (0.3, None))
    both = goal_tradeoff.likelihoods(brain([goal("A", 400_000, years=5, share=0.3), goal("B", 700_000, years=5)]), TODAY)
    check("the goal with no share draws on the other 70%", both["shared_capital"], 700_000.0)
    leaves = goal_tradeoff.likelihoods(brain([goal("Small", 10_000, years=2, share=0.5), goal("Later", 1_400_000, years=10)]), TODAY)
    alone = goal_tradeoff.likelihoods(brain([goal("Later", 1_400_000, years=10)], total=500_000.0), TODAY)
    check("what a matured goal leaves joins the pool", leaves["goals"][1]["success_probability"] > alone["goals"][0]["success_probability"], True)
    over = goal_tradeoff.likelihoods(brain([goal("A", 1, years=2, share=0.8), goal("B", 1, years=3, share=0.6)]), TODAY)
    check("shares over the whole are scaled, and say so", (over["shares_scaled"], round(sum(g["share"] for g in over["goals"]), 3)), (True, 1.0))
    order = goal_tradeoff.likelihoods(brain([goal("Second", 900_000, years=5, priority=2), goal("First", 900_000, years=5, priority=1)]), TODAY)
    check("the pool is drawn in priority order", [g["name"] for g in order["goals"]], ["First", "Second"])
    check("so the first is likelier", order["goals"][0]["success_probability"] > order["goals"][1]["success_probability"], True)

    print("\n=== Career earnings sit beside the goals and never fund them ===")
    goals = [goal("Retire", 3_000_000, years=10, kind="retirement")]
    none = goal_tradeoff.likelihoods(brain(goals), TODAY)
    check("no income on file: none, not one worked out from wealth", (none["earnings"]["basis"], none["earnings"]["value"]), ("not_on_file", 0))
    earner = {"full_name": "Ann", "date_of_birth": "1976-01-01", "profile": {"tax": {"annual_income": 300_000}}}
    paid = goal_tradeoff.likelihoods(brain(goals, [earner]), TODAY)
    check("a recorded income and date of birth are counted beside", (paid["earnings"]["basis"], paid["earnings"]["value"] > 0), ("recorded", True))
    check("and change no likelihood", paid["goals"][0]["success_probability"], none["goals"][0]["success_probability"])
    check("they stop where the retirement goal's date says", paid["earnings"]["earners"][0]["retirement_age"], 60)
    undated = goal_tradeoff.likelihoods(brain([goal("Retire", 1, kind="retirement")], [earner]), TODAY)
    check("with neither a retirement age nor a date, 65 is assumed and said", (undated["earnings"]["earners"][0]["retirement_age"], "retirement_age" in undated["earnings"]["assumed"]), (65, True))
    ageless = goal_tradeoff.likelihoods(brain(goals, [{"full_name": "Bo", "profile": {"tax": {"annual_income": 90_000}}}]), TODAY)
    check("an income with no date of birth is not counted, and says why", (ageless["earnings"]["value"], ageless["earnings"]["assumed"]), (0, ["age"]))

    print("\n=== The lever reads in the firm's currency ===")
    matrix = goal_tradeoff.compute_tradeoff_matrix(goals=[{"id": "g", "name": "G", "target_amount": 1, "years": 3}],
                                                   total_portfolio=100, allocation={"equity": 100}, ccy="GBP")
    check("add £5,000 a year", [lv["label"] for lv in matrix[0]["levers"] if lv["key"] == "contribute_5k"], ["Add £5,000 a year"])

    print("\n=== No copy of the old engine is left ===")
    sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in (ROOT / "backend" / "app").rglob("*.py")}
    copies = [n for n, src in sources.items() if re.search(r"funding_share\", 1\.0 /|total \* 0\.05\b|years\", 15\)", src)]
    check("no per-goal split, invented income or 15-year default anywhere", copies, [])
    callers = [n for n, src in sources.items() if "project_goal(" in src and not n.endswith("aurea_core/planning.py")]
    check("project_goal is left only to the ad hoc what-if route", callers, ["backend/app/api/core.py"])
    check("which has no household to disagree with", sources["backend/app/api/core.py"].count("project_goal("), 1)


async def surfaces() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Tui", slug="tui", base_currency="NZD", jurisdiction="NZ")
            s.add(firm)
            await s.flush()
            today = firm_time.today(firm)
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            bare = Household(id=uuid.uuid4(), firm_id=firm.id, name="Bare")
            s.add_all([hh, bare])
            await s.flush()
            mia = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Mia Lee", date_of_birth=date(1970, 6, 1),
                         profile={"tax": {"annual_income": 200_000}})
            ben = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=bare.id, full_name="Ben Bare")
            s.add_all([mia, ben])
            await s.flush()
            client = User(id=uuid.uuid4(), firm_id=firm.id, email="mia@t.test", full_name="Mia", hashed_password="x",
                          role=UserRole.CLIENT, person_id=mia.id)
            s.add(client)
            for household, person, value in ((hh, mia, 900_000), (bare, ben, 300_000)):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="M", mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                await s.flush()
                acc = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="A", custodian="C", currency="NZD", cash_balance=0)
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=f"F{household.name}", name="Fund", asset_class="equity",
                                  market_type=MarketType.PUBLIC, currency="NZD")
                s.add_all([acc, inst])
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=10, currency="NZD", source="test", is_real=True))
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acc.id, instrument_id=inst.id, quantity=value / 10,
                              market_value=value, cost_basis=value))
            s.add_all([
                Goal(firm_id=firm.id, household_id=hh.id, name="Retirement", kind="retirement", target_amount=1_600_000,
                     target_date=date(today.year + 8, 1, 1), assumptions={"funding_share": 0.7}),
                Goal(firm_id=firm.id, household_id=hh.id, name="Bach", kind="property", target_amount=300_000,
                     target_date=date(today.year + 4, 1, 1), assumptions={"funding_share": 0.3}),
                Goal(firm_id=firm.id, household_id=bare.id, name="Someday", kind="retirement", target_amount=900_000),
            ])
            await s.flush()

            print("\n=== Every screen gives a goal the same figure ===")
            canvas = {g["name"]: g["probability"] for g in (await client_view.home(s, hh.id, firm=firm))["goals"]}
            page = {g["name"]: g["success_probability"] for g in (await goal_tradeoff.for_household(s, hh.id, firm_id=firm.id))["goals"]}
            b = await household_brain(s, hh.id, firm_id=firm.id)
            agent = {g["name"]: g["probability"] for g in _signals.goal_projections(b, today)}
            staff = {g["goal"]: g["probability_of_success"] for g in (await core_api.household_planning(hh.id, firm=firm, db=s))["goals"]}
            check("the Canvas and the goals page", canvas, page)
            check("the agents", agent, page)
            check("the staff planning view", staff, page)
            check("two goals, each read", sorted(page), ["Bach", "Retirement"])
            signals = [x["title"] for x in _signals.book_signals(b) if x["kind"] == "goal_gap"]
            check("an agent's off-track goals are the page's", sorted(t.removeprefix("Goal off-track: ") for t in signals),
                  sorted(n for n, p in page.items() if p < 0.70))

            print("\n=== The client is told what the figures rest on ===")
            me = ToolExecutors(s, client.id, UserRole.CLIENT, firm.id)
            goals = await me.execute("read_my_goals", {})
            check("the goals tool says they are the goals page's figures", "same figures as the goals page" in goals["message"], True)
            check("and that earnings are not savings", "not counted as savings" in goals["message"], True)
            wealth = await me.execute("read_my_wealth", {})
            check("the home page's count is the goals page's", wealth["goals_on_track"], sum(1 for p in page.values() if p >= 0.70))

            print("\n=== The retirement engine answers its own question, and says what it assumed ===")
            plan = await retirement.for_household(s, hh.id, firm_id=firm.id)
            check("it measures whether the income lasts", plan["measures"], "retirement_income")
            check("in the firm's currency", (plan["currency"], (await goal_tradeoff.for_household(s, hh.id, firm_id=firm.id))["currency"]), ("NZD", "NZD"))
            check("with a date of birth, a date and a share on file, only these are assumed", sorted(plan["assumed"]),
                  ["annual_income", "longevity_age"])
            check("retiring at the age reached on the goal's date", plan["retirement_age"],
                  goal_tradeoff.age_of("1970-06-01", today) + goal_tradeoff.horizon({"target_date": f"{today.year + 8}-01-01"}, today)[0])
            spare = await retirement.for_household(s, bare.id, firm_id=firm.id)
            check("with nothing on file each stand-in is named", sorted(spare["assumed"]),
                  ["annual_income", "current_age", "funding_share", "longevity_age", "retirement_age"])
            told = await me.execute("read_my_retirement", {})
            check("the client is told it is a different question", "different question" in told["message"], True)
            check("and what was assumed", "Assumed, not on file" in told["message"], True)
    finally:
        await db.dispose()


def main() -> None:
    pure()
    asyncio.run(surfaces())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:\n  " + "\n  ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
