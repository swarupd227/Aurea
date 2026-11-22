"""Prove four planning inputs (B-182, B-195, B-211, B-212):

- every planning fact carries a date, and the plan shows its oldest input, how many are older than the firm's line
  (90 days, named as assumed, unless the firm sets one) and what it reads that is not on file, never filling it;
- beside the first-year crash, one return sequence and its reverse run on the household's own pot and draw,
  deterministic and labelled an illustration;
- an adviser records whether an existing goal's target is in today's money and its index, behind its Confirm card,
  and nothing else on the goal changes;
- a stream's kind sets whether it rises with inflation when nobody says: Social Security and state pensions do.

Runs against a throwaway SQLite database.

    python -m tests.verify_planning_inputs
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import financial_plan as fp, firm_time, goal_tradeoff, plan_inputs, planning, protection, retirement
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.balance_sheet import BalanceItem
from app.models.enums import MarketType, UserRole
from app.models.financial_plan import IncomeStream
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def near(label: str, got, want, within=0.01) -> None:
    ok = got is not None and abs(got - want) <= within
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want about {want}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


ALLOC = {"equity": 600_000.0, "fixed_income": 400_000.0}


def pure() -> None:
    print("\n=== B-195: one return sequence and its reverse, on the household's pot and draw ===")
    returns = retirement.illustration_returns(0.06, 0.12, 30)
    near("the returns average the expected return", sum(returns) / len(returns), 0.06, 1e-9)
    check("weakest first", returns == sorted(returns), True)
    ill = retirement.sequence_illustration(1_000_000, [55_000.0] * 30, mu=0.06, sigma=0.12, retirement_age=65)
    weak, strong = ill["orders"]
    print(f"     weak years first: runs out at {weak['depletes_at']}, ends {weak['ending_balance']}; reversed: runs out at {strong['depletes_at']}, ends {strong['ending_balance']}")
    check("labelled an illustration, on the plan's return and volatility", (ill["illustration"], ill["basis"], ill["average_return"]), (True, "plan_return_and_volatility", 0.06))
    check("the same returns in each order", (weak["order"], strong["order"], weak["first_year_return"] < 0.06 < strong["first_year_return"]), ("weak_first", "strong_first", True))
    check("weak years first runs out; the reverse does not", (weak["depletes_at"] is not None, strong["depletes_at"]), (True, None))
    check("so the reverse ends with money and the weak-first order with none", (weak["ending_balance"], strong["ending_balance"] > 1_000_000), (0, True))
    check("it is deterministic", retirement.sequence_illustration(1_000_000, [55_000.0] * 30, mu=0.06, sigma=0.12, retirement_age=65), ill)

    common = dict(current_value=1_000_000, allocation=ALLOC, current_age=60, retirement_age=65, longevity_age=95, annual_income=55_000)
    one, two = retirement.retirement_plan(**common), retirement.retirement_plan(**common)
    seq = one["sequence_risk"]
    check("it sits beside the first-year crash", sorted(seq), ["baseline", "crash_return", "delta", "early_crash", "illustration"])
    check("on the plan's own pot, over its years in retirement", (seq["illustration"]["pot"], seq["illustration"]["years"]), (one["projected_at_retirement"], 30))
    near("at the plan's expected return", seq["illustration"]["average_return"], one["assumptions"]["expected_return"], 0.0001)
    check("the base figures draw nothing new: two runs agree", (one["success_probability"], one["sequence_risk"]), (two["success_probability"], two["sequence_risk"]))
    draws = retirement._draws(55_000, [], acc=5, dec=30, inflation=0.025)
    mu, sigma = planning._blended_assumptions(ALLOC)
    base = retirement._simulate(1_000_000, mu, sigma, acc_years=5, draws=draws, annual_contribution=0.0, sims=2500, seed=7)
    check("and the success figure is the simulation's, as before", one["success_probability"], round(base["success"], 3))


async def surfaces() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            us = Firm(id=uuid.uuid4(), name="U", slug="u", base_currency="USD", jurisdiction="US", settings={})
            s.add(us)
            await s.flush()
            today = firm_time.today(us)
            adviser = User(id=uuid.uuid4(), firm_id=us.id, email="a@u", full_name="Ada Adviser", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            h = Household(id=uuid.uuid4(), firm_id=us.id, name="Lee")
            s.add(h)
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, full_name="Ann Lee", date_of_birth=date(today.year - 60, 1, 1),
                         profile={"tax": {"ira_value": 50_000}})
            bo = Person(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, full_name="Bo Lee")
            s.add_all([ann, bo])
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=us.id, person_id=ann.id, name="M", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            acct = Account(id=uuid.uuid4(), firm_id=us.id, mandate_id=m.id, name="A", currency="USD", cash_balance=0)
            inst = Instrument(id=uuid.uuid4(), firm_id=us.id, symbol="XLEE", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add_all([acct, inst])
            await s.flush()
            s.add(Price(firm_id=us.id, instrument_id=inst.id, as_of=today, close=100, currency="USD", source="test", is_real=True))
            s.add(Holding(firm_id=us.id, account_id=acct.id, instrument_id=inst.id, quantity=10_000, market_value=1_000_000, cost_basis=1_000_000))
            s.add(Goal(firm_id=us.id, household_id=h.id, name="Retirement", kind="retirement", target_amount=1_000_000, target_date=date(today.year + 5, 1, 1), assumptions={}))
            await s.flush()
            ex = ToolExecutors(s, adviser.id, adviser.role, us.id)

            async def card(tool, inputs):
                return {r["label"]: r["value"] for r in (await ex.preview_change(tool, inputs))["rows"]}

            print("\n=== B-212: a stream's kind sets whether it rises with inflation ===")
            social = {"person_id": str(ann.id), "kind": "social_security", "annual_amount": 30_000, "start_age": 67, "source_document": "SSA statement 2026"}
            shown = await card("record_income_stream", social)
            check("Social Security with nothing said: linked, and the card says it is the default", shown["Rises with inflation"], "Yes · the default for social security")
            check("a state pension likewise", (await card("record_income_stream", {**social, "kind": "state_pension"}))["Rises with inflation"], "Yes · the default for state pension")
            check("an employer pension defaults to level, and the card shows it", (await card("record_income_stream", {**social, "kind": "employer_pension"}))["Rises with inflation"],
                  "No · the default for employer pension")
            check("a stated answer wins over the default", (await card("record_income_stream", {**social, "inflation_linked": False}))["Rises with inflation"], "No")
            check("and is shown as stated", (await card("record_income_stream", {**social, "kind": "annuity", "inflation_linked": "true"}))["Rises with inflation"], "Yes")
            await refused("an answer that is no answer", ex.preview_change("record_income_stream", {**social, "inflation_linked": "maybe"}), "true or false")
            check("showing the card records nothing", (await s.execute(select(IncomeStream))).scalars().all(), [])
            await ex.record_income_stream(social)
            await ex.record_income_stream({**social, "kind": "employer_pension", "name": "Pension", "start_age": 65})
            rows = {r.kind: r.inflation_linked for r in (await s.execute(select(IncomeStream))).scalars().all()}
            check("recorded: Social Security linked, the pension level", rows, {"social_security": True, "employer_pension": False})
            # Before B-212 a Social Security benefit entered without the flag was level, so it bought less each year.
            plan = await retirement.for_household(s, h.id, firm_id=us.id)
            ss = next(st for st in (await fp.retirement_inputs(s, h.id, firm_id=us.id, retirement_age=65, current_age=60))["streams"] if st["name"] != "Pension")
            check("the projection reads the benefit as linked", ss["inflation_linked"], True)

            print("\n=== B-182: every planning fact carries a date ===")
            s.add_all([
                BalanceItem(firm_id=us.id, household_id=h.id, kind="essential_spending", side="expense", name="Spending", amount=60_000, currency="USD",
                            as_of=today - timedelta(days=200), status="current", source_document="Budget sheet", recorded_by="t"),
                BalanceItem(firm_id=us.id, household_id=h.id, kind="cash", side="asset", name="Savings", amount=40_000, currency="USD",
                            as_of=today - timedelta(days=10), status="current", source_document="Bank statement", recorded_by="t"),
            ])
            await s.flush()
            await protection.record_policy(s, us.id, person_id=ann.id, kind="life", provider="Acme Life", source_document="Policy schedule 2026",
                                           sum_insured=500_000, recorded_by="t")
            policy = (await s.execute(select(ProtectionPolicy))).scalars().one()
            check("a recorded policy is dated the firm's day", policy.as_of, today)
            older = ProtectionPolicy(firm_id=us.id, household_id=h.id, person_id=ann.id, kind="tpd", provider="Old Co", sum_insured=100_000, currency="USD",
                                     status="in_force", source_document="Old schedule", recorded_by="t",
                                     created_at=datetime.combine(today - timedelta(days=400), datetime.min.time(), tzinfo=timezone.utc))
            s.add(older)
            await s.flush()
            rows = {r["provider"]: r["as_of"] for r in (await protection.for_household(s, h.id, firm_id=us.id))["policies"]}
            check("one recorded before dates were kept reads the day it was created", rows, {"Acme Life": today.isoformat(), "Old Co": (today - timedelta(days=400)).isoformat()})
            await protection.record_income(s, us.id, person_id=ann.id, annual_income=120_000, source_document="Payslip, Oct 2026", recorded_by="t")
            check("a recorded income is dated beside its field", ann.profile["tax"]["as_of"], {"annual_income": today.isoformat()})

            inputs = await plan_inputs.for_household(s, h.id, firm_id=us.id)
            print(f"     {inputs['inputs']} inputs, oldest {inputs['oldest']['as_of']} ({inputs['oldest']['name']}), {inputs['older_than_line']} older than {inputs['line_days']} days, {inputs['undated']} undated")
            check("every fact counted: two items, two streams, two policies, the income and the IRA", (inputs["inputs"], inputs["dated"], inputs["undated"]), (8, 7, 1))
            check("the IRA figure carries no date and is not given one", inputs["undated_inputs"], [{"kind": "tax_field", "name": "ira_value", "person": "Ann Lee", "as_of": None, "days": None}])
            check("the oldest input is the four-hundred-day-old policy", (inputs["oldest"]["name"], inputs["oldest"]["days"]), ("Old Co tpd", 400))
            check("two are older than the line: 90 days, named as assumed", (inputs["older_than_line"], inputs["line_days"], inputs["line_assumed"]), (2, 90, True))
            us.settings = {"plan_input_days": 7}
            await s.flush()
            tight = await plan_inputs.for_household(s, h.id, firm_id=us.id)
            check("the firm's own line is used and not named as assumed", (tight["older_than_line"], tight["line_days"], tight["line_assumed"]), (3, 7, False))
            us.settings = {}
            await s.flush()
            missing = [(m["fact"], m.get("person") or m.get("goal")) for m in inputs["missing"]]
            check("what the plan reads and is not on file is listed", missing, [("date_of_birth", "Bo Lee"), ("annual_income", "Bo Lee"), ("plan_to_age", None), ("funding_share", None)])
            check("completeness against what it reads", inputs["completeness"], {"read": 9, "on_file": 5, "share": 0.556})
            check("nothing is filled: Bo still has no date of birth or income", (bo.date_of_birth, (bo.profile or {}).get("tax")), (None, None))
            plan = await fp.for_household(s, h.id, firm_id=us.id)
            check("the financial plan carries it", plan["inputs"], inputs)
            check("and so does the retirement view", (await retirement.for_household(s, h.id, firm_id=us.id))["inputs"], inputs)
            check("the retirement projection still names what it stood in", {"longevity_age", "funding_share"} <= set(plan["retirement"]["assumed"]), True)
            check("and shows the sequence illustration on the household's pot", plan["retirement"]["sequence_risk"]["illustration"]["pot"], plan["retirement"]["projected_at_retirement"])
            read = await ex.read_financial_plan({"household_id": str(h.id)})
            check("the conversation is told how old and what is missing", ("2 input(s) older than 90 days" in read["message"], "date_of_birth (Bo Lee)" in read["message"]), (True, True))

            print("\n=== B-211: a goal's target basis and index, behind its Confirm card ===")
            boat = Goal(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, name="Boat", kind="other", target_amount=100_000, priority=3, assumptions={"years": 10, "note": "kept"})
            school = Goal(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, name="Education · Mia", kind="education", target_amount=120_000,
                          target_date=date(today.year + 5, 1, 1), priority=2,
                          assumptions={"student": "Mia", "start_year": today.year + 5, "years": 4, "annual_cost": 30_000, "target_basis": "today", "index": "education"})
            s.add_all([boat, school])
            await s.flush()
            before = next(g for g in (await goal_tradeoff.for_household(s, h.id, firm_id=us.id))["goals"] if g["name"] == "Boat")
            shown = await card("update_goal", {"goal_id": str(boat.id), "target_basis": "today"})
            check("the card shows the basis before and after", shown["Target basis"], "Not recorded (read as stated) becomes Today's money")
            check("and the index it will grow with, the kind's own when none is named", shown["Grows with"], "Nothing becomes CPI")
            check("and nothing else", ("Target" in shown, "Date" in shown, "Priority" in shown), (False, False, False))
            check("showing it changes nothing", boat.assumptions, {"years": 10, "note": "kept"})
            await refused("an index on a fixed sum", ex.preview_change("update_goal", {"goal_id": str(boat.id), "index": "healthcare"}), "only to a target in today's money")
            await refused("an unknown basis", ex.preview_change("update_goal", {"goal_id": str(boat.id), "target_basis": "real"}), "target_basis must be")
            await refused("an unknown index", ex.preview_change("update_goal", {"goal_id": str(boat.id), "target_basis": "today", "index": "wages"}), "index must be one of")
            await refused("a school's fees as a fixed sum", ex.preview_change("update_goal", {"goal_id": str(school.id), "target_basis": "nominal"}), "cannot be a fixed sum")
            await refused("naming what it already is", ex.preview_change("update_goal", {"goal_id": str(school.id), "index": "education"}), "say what to change")
            done = await ex.update_goal({"goal_id": str(boat.id), "target_basis": "today"})
            check("recorded in the goal's assumptions, the rest kept", boat.assumptions, {"years": 10, "note": "kept", "target_basis": "today", "index": "cpi"})
            check("nothing else on the goal changed", (float(boat.target_amount), boat.priority, boat.target_date, boat.name), (100_000.0, 3, None, "Boat"))
            check("answered with what changed", done["updates"], {"target_basis": "today", "index": "cpi"})
            print(f"     Boat: target {before['target_amount']} as stated, {before['success_probability']:.0%} likely -> {done['goal']['target_amount']} by its date, {done['goal']['success_probability']:.0%} likely")
            near("the goals page now grows it with CPI for ten years", done["goal"]["target_amount"], 100_000 * 1.025 ** 10, 0.02)
            check("named as today's money", (done["goal"]["target_basis"], done["goal"]["target_today"], done["goal"]["index"], before["target_basis"]), ("today", 100_000.0, "cpi", "as_stated"))
            check("one goal, one likelihood: the card's is the page's", done["goal"]["success_probability"],
                  next(g for g in (await goal_tradeoff.for_household(s, h.id, firm_id=us.id))["goals"] if g["name"] == "Boat")["success_probability"])
            shown = await card("update_goal", {"goal_id": str(boat.id), "index": "healthcare"})
            check("a new index is shown from and to", (shown["Grows with"], "Target basis" in shown), ("CPI becomes Healthcare inflation", False))
            done = await ex.update_goal({"goal_id": str(boat.id), "index": "healthcare"})
            near("and grows at the healthcare rate", done["goal"]["target_amount"], 100_000 * (1 + planning.INFLATION_INDEXES["healthcare"][1]) ** 10, 0.02)
            shown = await card("update_goal", {"goal_id": str(boat.id), "target_basis": "nominal"})
            check("back to a fixed sum: its index goes with it", (shown["Target basis"], shown["Grows with"]), ("Today's money becomes A fixed sum", "Healthcare inflation becomes Nothing"))
            done = await ex.update_goal({"goal_id": str(boat.id), "target_basis": "nominal"})
            check("recorded as a fixed sum", (boat.assumptions, done["goal"]["target_amount"], done["goal"]["target_basis"]),
                  ({"years": 10, "note": "kept", "target_basis": "nominal"}, 100_000.0, "as_stated"))
            done = await ex.update_goal({"goal_id": str(school.id), "index": "cpi"})
            check("a school's fees may be put on another index", (school.assumptions["index"], done["goal"]["index"]), ("cpi", "cpi"))
    finally:
        await db.dispose()


def main() -> int:
    pure()
    asyncio.run(surfaces())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
