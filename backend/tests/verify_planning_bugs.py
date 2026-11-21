"""Prove five planning bugs stay fixed (B-167, B-168, B-169, B-172, B-173):

- the firm's capital market assumptions reach every projection, and each says whose they are;
- the withdrawal rate, the sustainable rate and the 4% rule are in one money, today's;
- a pension that does not rise with inflation buys less each year, and one starting later is bridged;
- a goal's target in today's money grows with its own inflation, and the plan's education row is the goals page's;
- a couple's plan runs until the younger adult reaches the plan-to age, and says whose ages it counts.

Runs against a throwaway SQLite database.

    python -m tests.verify_planning_bugs
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import core as core_api
from app.aurea_core import financial_plan as fp, firm_time, goal_tradeoff, planning, retirement
from app.core.db import Base
from app.models.enums import MarketType
from app.models.financial_plan import IncomeStream
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price
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


ALLOC = {"equity": 600_000.0, "fixed_income": 400_000.0}
LOW_EQUITY = {"cmas": {"equity": [0.02, 0.16]}}


def pure() -> None:
    print("\n=== B-167: the firm's capital market assumptions move a success figure ===")
    common = dict(current_value=1_000_000, allocation=ALLOC, current_age=60, retirement_age=65, longevity_age=95, annual_income=55_000)
    platform = retirement.retirement_plan(**common)
    firm = retirement.retirement_plan(**common, cma=planning.effective_cma(LOW_EQUITY))
    print(f"     success on the platform's CMAs {platform['success_probability']}, on equity at 2% {firm['success_probability']}")
    check("a lower equity return lowers the success figure", firm["success_probability"] < platform["success_probability"], True)
    near("and the expected return is the firm's", firm["assumptions"]["expected_return"], 0.6 * 0.02 + 0.4 * 0.035, 0.0001)
    check("whose they are: none set is the platform's", (planning.cma_source(None), planning.cma_source({"cmas": {}})), ("platform", "platform"))
    check("one set is the firm's", planning.cma_source(LOW_EQUITY), "firm")
    check("a malformed one is not", planning.cma_source({"cmas": {"equity": ["x", "y"]}}), "platform")

    def brain(goals, settings=None, persons=None, scale=1.0):
        return {"household": {"id": "h", "name": "T"}, "currency": "USD", "persons": persons or [], "goals": goals,
                "totals": {"total_value": 1_000_000.0 * scale, "by_asset_class": {k: v * scale for k, v in ALLOC.items()}},
                "planning": planning.assumption_set(settings)}

    home = [{"id": "g", "name": "Home", "kind": "property", "target_amount": 1_300_000, "assumptions": {"years": 8}}]
    base = goal_tradeoff.likelihoods(brain(home), date(2026, 11, 21))
    low = goal_tradeoff.likelihoods(brain(home, LOW_EQUITY), date(2026, 11, 21))
    print(f"     goal likelihood on the platform's CMAs {base['goals'][0]['success_probability']}, on the firm's {low['goals'][0]['success_probability']}")
    check("the goals engine reads the brain's CMAs", low["goals"][0]["success_probability"] < base["goals"][0]["success_probability"], True)
    check("and says whose", (base["assumptions_source"], low["assumptions_source"], low["assumptions"]["source"]), ("platform", "firm", "firm"))

    print("\n=== B-169: a level pension buys less each year; a later one is bridged ===")
    draws = retirement._draws(60_000, [{"annual": 20_000, "starts_in": 0, "inflation_linked": False}], acc=0, dec=3, inflation=0.025)
    check("a level stream is taken off at its stated amount", [round(d, 2) for d in draws], [40_000.0, 41_500.0, 43_037.5])
    linked = retirement._draws(60_000, [{"annual": 20_000, "starts_in": 0, "inflation_linked": True}], acc=0, dec=3, inflation=0.025)
    check("a linked stream keeps its buying power", [round(d, 2) for d in linked], [40_000.0, 41_000.0, 42_025.0])
    later = retirement._draws(60_000, [{"annual": 20_000, "starts_in": 2, "inflation_linked": True}], acc=0, dec=3, inflation=0.025)
    check("one starting two years in is not netted until it starts", [round(d, 2) for d in later], [60_000.0, 61_500.0, 42_025.0])
    stream = dict(current_value=900_000, allocation=ALLOC, current_age=60, retirement_age=65, longevity_age=95, annual_income=70_000)
    lvl = retirement.retirement_plan(**stream, streams=[{"annual": 25_000, "starts_in": 5, "inflation_linked": False}])
    idx = retirement.retirement_plan(**stream, streams=[{"annual": 25_000, "starts_in": 5, "inflation_linked": True}])
    # Before the fix both were run as a 45,000 draw that kept pace with inflation, so both read idx's figure.
    print(f"     success with a level 25,000 pension {lvl['success_probability']}, with one linked to inflation {idx['success_probability']}")
    check("a level pension's success is lower than a linked one's", lvl["success_probability"] < idx["success_probability"], True)
    near("at retirement it buys 25,000 / 1.025^5 in today's money", lvl["guaranteed_at_retirement"], 25_000 / 1.025 ** 5, 0.01)
    check("so the portfolio's first draw is the rest of the need", lvl["income_target"], round(70_000 - 25_000 / 1.025 ** 5))
    check("a linked one counts in full", (idx["guaranteed_at_retirement"], idx["income_target"]), (25_000.0, 45_000))
    bridge = retirement.retirement_plan(**stream, streams=[{"annual": 25_000, "starts_in": 10, "inflation_linked": True}])
    check("one starting five years after retirement: the first draw is the whole need", (bridge["guaranteed_at_retirement"], bridge["income_target"]), (0, 70_000))
    check("and it still helps, from when it starts", idx["success_probability"] >= bridge["success_probability"] > retirement.retirement_plan(**stream)["success_probability"], True)

    print("\n=== B-172: a goal's target grows with its own inflation ===")
    today = date(2026, 11, 21)
    school = {"id": "e", "name": "Education · Mia", "kind": "education", "target_amount": 120_000, "target_date": "2031-01-01",
              "assumptions": {"student": "Mia", "start_year": 2031, "years": 4, "annual_cost": 30_000}}
    flat = {**school, "assumptions": {"student": "Mia"}}
    grown = goal_tradeoff.likelihoods(brain([school], scale=0.11), today)["goals"][0]
    stated = goal_tradeoff.likelihoods(brain([flat], scale=0.11), today)["goals"][0]
    want = sum(30_000 * 1.05 ** (5 + k) for k in range(4))
    near("the target is four years of fees grown at 5% from 2031", grown["target_amount"], want, 0.02)
    check("named as today's money, by the education index", (grown["target_basis"], grown["index"], grown["target_today"]), ("today", "education", 120_000))
    check("the default rate is named as assumed", "education_inflation" in grown["assumed"], True)
    # Before the fix the goals page ran this goal on 120,000 as stated, so it read the flat figure.
    print(f"     with 110,000 invested: likelihood on 120,000 as stated {stated['success_probability']}, grown to {round(grown['target_amount'])} {grown['success_probability']}")
    check("the grown target is less likely than the flat one", grown["success_probability"] < stated["success_probability"], True)
    firm_rate = goal_tradeoff.likelihoods(brain([school], {"education_inflation": 0.03}), today)["goals"][0]
    near("the firm's education inflation is used", firm_rate["target_amount"], sum(30_000 * 1.03 ** (5 + k) for k in range(4)), 0.02)
    check("and is not named as assumed", "education_inflation" in firm_rate["assumed"], False)
    cpi_goal = {"id": "c", "name": "Boat", "kind": "other", "target_amount": 100_000, "assumptions": {"years": 10, "target_basis": "today"}}
    near("a target in today's money with no index grows with CPI", goal_tradeoff.likelihoods(brain([cpi_goal]), today)["goals"][0]["target_amount"], 100_000 * 1.025 ** 10, 0.02)
    plain = {"id": "p", "name": "Car", "kind": "other", "target_amount": 50_000, "assumptions": {"years": 3}}
    check("a goal with no basis recorded is read as stated", goal_tradeoff.likelihoods(brain([plain]), today)["goals"][0]["target_amount"], 50_000)
    later = goal_tradeoff.likelihoods(brain([school]), today, goal_overrides={"e": {"years_delta": 2}})["goals"][0]
    near("putting it off two years grows it two more years", later["target_amount"], want * 1.05 ** 2, 0.02)


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

            async def client(name, dobs, *, value=1_000_000):
                h = Household(id=uuid.uuid4(), firm_id=us.id, name=name)
                s.add(h)
                await s.flush()
                people = [Person(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, full_name=f"{name} {who}", date_of_birth=dob) for who, dob in dobs]
                s.add_all(people)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=us.id, person_id=people[0].id, name="M", mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=us.id, mandate_id=m.id, name="A", currency="USD", cash_balance=0)
                i = Instrument(id=uuid.uuid4(), firm_id=us.id, symbol=f"X{name[:3].upper()}", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
                s.add_all([a, i])
                await s.flush()
                s.add(Price(firm_id=us.id, instrument_id=i.id, as_of=today, close=100, currency="USD", source="test", is_real=True))
                s.add(Holding(firm_id=us.id, account_id=a.id, instrument_id=i.id, quantity=value / 100, market_value=value, cost_basis=value))
                await s.flush()
                return h, people

            def born(age):
                return date(today.year - age, 1, 1)

            print("\n=== B-167: every engine reads the firm's CMAs ===")
            lee, _ = await client("Lee", [("Ann", born(60))])
            s.add(Goal(firm_id=us.id, household_id=lee.id, name="Home", kind="property", target_amount=1_300_000, target_date=date(today.year + 8, 1, 1), assumptions={}))
            await s.flush()
            before = await retirement.for_household(s, lee.id, firm_id=us.id)
            goals_before = (await goal_tradeoff.for_household(s, lee.id, firm_id=us.id))["goals"][0]["success_probability"]
            plan_before = await fp.for_household(s, lee.id, firm_id=us.id)
            risk_before = (await core_api.household_planning(lee.id, firm=us, db=s))["risk"]["expected_return"]
            us.settings = dict(LOW_EQUITY)
            await s.flush()
            after = await retirement.for_household(s, lee.id, firm_id=us.id)
            goals_after = (await goal_tradeoff.for_household(s, lee.id, firm_id=us.id))["goals"][0]["success_probability"]
            plan_after = await fp.for_household(s, lee.id, firm_id=us.id)
            planning_after = await core_api.household_planning(lee.id, firm=us, db=s)
            print(f"     retirement success {before['success_probability']} -> {after['success_probability']}; goal {goals_before} -> {goals_after}")
            check("retirement: a changed CMA moves the success figure", after["success_probability"] < before["success_probability"], True)
            check("and the plan says whose", (before["assumptions_source"], after["assumptions_source"]), ("platform", "firm"))
            check("the goals page moves too", goals_after < goals_before, True)
            check("the financial plan says whose", (plan_before["assumptions_source"], plan_after["assumptions_source"]), ("platform", "firm"))
            check("and its success figure moved with it", plan_after["retirement"]["success_probability"] < plan_before["retirement"]["success_probability"], True)
            check("the staff planning view's risk is the firm's", (risk_before, planning_after["risk"]["expected_return"], planning_after["assumptions_source"]), (0.075, 0.02, "firm"))
            what_if = await core_api.ad_hoc_projection({"current_value": 100_000, "allocation": {"equity": 1}, "years": 10, "target_amount": 150_000}, firm=us, db=s)
            check("the what-if projection is on them too", what_if["assumptions_source"], "firm")
            us.settings = {}
            await s.flush()

            print("\n=== B-168: the rates are in today's money ===")
            plan = await fp.for_household(s, lee.id, firm_id=us.id)
            r = plan["retirement"]
            infl = (1.025) ** r["years_to_retirement"]
            near("the pot in today's money is the projected pot less the inflation to then", r["projected_at_retirement_today"], r["projected_at_retirement"] / infl, 1)
            near("the withdrawal rate is the income over the pot, both today's", r["withdrawal_rate"], r["income_target"] / r["projected_at_retirement_today"], 0.0001)
            old = r["income_target"] / r["projected_at_retirement"]
            print(f"     withdrawal rate {r['withdrawal_rate']} (it was {round(old, 4)}, income in today's money over a pot in {today.year + r['years_to_retirement']}'s)")
            near("which is the old figure times the inflation to retirement", r["withdrawal_rate"], old * infl, 0.0002)
            near("the sustainable rate likewise", r["sustainable_rate"], r["sustainable_income"] / r["projected_at_retirement_today"], 0.0001)
            check("the 4% rule is on the same pot", r["rule_of_thumb_income"], round(r["projected_at_retirement_today"] * 0.04))
            check("and the plan names the money", r["money_basis"], "today")

            print("\n=== B-169: a level pension on file ===")
            level, (pat,) = await client("Level", [("Pat", born(60))])
            pension = IncomeStream(firm_id=us.id, household_id=level.id, person_id=pat.id, kind="employer_pension", name="Pension", annual_amount=30_000,
                                   start_age=65, inflation_linked=False, source_document="Pension statement", as_of=today, recorded_by="t")
            s.add(pension)
            await s.flush()
            flat = await retirement.for_household(s, level.id, firm_id=us.id)
            pension.inflation_linked = True
            await s.flush()
            linked = await retirement.for_household(s, level.id, firm_id=us.id)
            print(f"     success with the pension level {flat['success_probability']}, linked {linked['success_probability']}")
            check("the level pension's plan is less likely to last", flat["success_probability"] < linked["success_probability"], True)
            near("and counts what it buys at retirement", flat["guaranteed_income"], 30_000 / 1.025 ** 5, 0.01)
            check("a linked one counts in full", linked["guaranteed_income"], 30_000.0)
            spouse, (sam, sue) = await client("Spouse", [("Sam", born(62)), ("Sue", born(55))])
            s.add(IncomeStream(firm_id=us.id, household_id=spouse.id, person_id=sue.id, kind="employer_pension", name="Sue's pension", annual_amount=20_000,
                               start_age=66, inflation_linked=True, source_document="Pension statement", as_of=today, recorded_by="t"))
            await s.flush()
            sp = await retirement.for_household(s, spouse.id, firm_id=us.id)
            # Sam retires at 67, in five years; Sue's pension starts at her 66, in eleven. Before, her 66 was read
            # against Sam's 67 and counted from the first year of retirement.
            check("a spouse's stream starts on her own age, and is bridged until then", (sp["years_to_retirement"], sp["guaranteed_income"], sp["starts_later"]), (5, 0, 20_000.0))

            print("\n=== B-172: the plan's education row is the goals page's ===")
            await fp.record_education(s, lee.id, firm_id=us.id, by="t", student="Mia", start_year=today.year + 5, years=4, annual_cost=30_000, cost_source="University published fees")
            plan = await fp.for_household(s, lee.id, firm_id=us.id)
            edu = plan["education"][0]
            page = {g["name"]: g for g in (await goal_tradeoff.for_household(s, lee.id, firm_id=us.id))["goals"]}["Education · Mia"]
            check("one goal, one likelihood", edu["likelihood"], page["success_probability"])
            check("one cost", edu["total_cost"], round(page["target_amount"]))
            near("grown with education inflation", edu["total_cost"], sum(30_000 * 1.05 ** (5 + k) for k in range(4)), 1)
            check("today's money and the 529s beside it", (edu["total_cost_today"], edu["in_529"], edu["index"]), (120_000.0, 0, "education"))
            goal = (await s.execute(Goal.__table__.select().where(Goal.household_id == lee.id, Goal.kind == "education"))).first()
            check("recorded as today's money, by its index", (goal.assumptions["target_basis"], goal.assumptions["index"]), ("today", "education"))

            print("\n=== B-173: a couple's plan runs to the younger's plan-to age ===")
            couple, _ = await client("Couple", [("Al", born(70)), ("Bea", born(60))])
            single, _ = await client("Single", [("Cy", born(70))])
            two = await retirement.for_household(s, couple.id, firm_id=us.id)
            one = await retirement.for_household(s, single.id, firm_id=us.id)
            print(f"     years in retirement: one at 70 {one['years_in_retirement']}, a couple at 70 and 60 {two['years_in_retirement']} (it was {one['years_in_retirement']})")
            check("the single adult's plan runs to 95", (one["current_age"], one["retirement_age"], one["years_in_retirement"], one["end_age"]), (70, 75, 20, 95))
            check("the couple's runs ten years longer, to Bea's 95", (two["current_age"], two["years_in_retirement"], two["end_age"]), (70, 30, 105))
            check("and names whose ages and whose plan-to age", (two["ages_of"], two["plan_to"]),
                  ("Couple Al", {"age": 95, "person": "Couple Bea", "whose": "younger_adult", "assumed": True}))
            check("95 is named as assumed", "longevity_age" in two["assumed"], True)
            check("so it is less likely to last", two["success_probability"] < one["success_probability"], True)
            what_if = await retirement.for_household(s, couple.id, firm_id=us.id, overrides={"longevity_age": 90})
            check("a plan-to age asked about is the younger's", (what_if["longevity_age"], what_if["end_age"], what_if["plan_to"]["assumed"]), (90, 100, False))
            fplan = await fp.for_household(s, couple.id, firm_id=us.id)
            check("the financial plan carries whose", (fplan["retirement"]["plan_to"]["person"], fplan["retirement"]["ages_of"]), ("Couple Bea", "Couple Al"))
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
