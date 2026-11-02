"""Prove the plan works from what is on file: nothing recorded changes nothing, spending and debts become the income
to plan for, income already counted is not drawn twice, Social Security's arithmetic is the law's and is the US's
alone, and a figure that cannot be worked out reads as not assessed.

Runs against a throwaway SQLite database.

    python -m tests.verify_financial_plan
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import balance_sheet, financial_plan as fp, firm_time, retirement
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.financial_plan import IncomeStream
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def near(label: str, got, want, within=0.01) -> None:
    ok = got is not None and abs(got - want) <= within
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want about {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, fp.PlanError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    print("\n=== Social Security's arithmetic ===")
    near("62 with a full age of 67 pays 70%", fp.claim_factor(67 * 12, 62 * 12), 0.70, 0.0001)
    near("70 pays 124%", fp.claim_factor(67 * 12, 70 * 12), 1.24, 0.0001)
    near("waiting past 70 adds nothing", fp.claim_factor(67 * 12, 72 * 12), 1.24, 0.0001)
    near("one year early is 6.67% off", fp.claim_factor(67 * 12, 66 * 12), 0.9333, 0.0001)
    check("full age by birth year", (fp.full_retirement_months(1950), fp.full_retirement_months(1957), fp.full_retirement_months(1965)), (792, 798, 804))
    table = fp.claiming_table(36000, 1965)
    check("the table", [(c["age"], c["annual"]) for c in table["claims"]], [("62", 25200.0), ("67", 36000.0), ("70", 44640.0)])
    near("67 beats 62 by about 78.7", table["claims"][1]["breaks_even"]["62"], 78.7, 0.05)
    near("the pension's implied return, paid from the first day", fp.implied_return(100000, 10000, 20), 0.0892, 0.0005)
    check("a lump sum beyond what the income could ever pay", fp.implied_return(1000000, 1000, 10), None)

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            us = Firm(id=uuid.uuid4(), name="U", slug="u", base_currency="USD", jurisdiction="US")
            nz = Firm(id=uuid.uuid4(), name="N", slug="n", base_currency="NZD", jurisdiction="NZ", settings={"reserve_months": 3})
            s.add_all([us, nz])
            await s.flush()
            today = firm_time.today(us)

            async def client(firm, name, dob, *, cash=50000, value=950000):
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(h)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=f"{name} One", date_of_birth=dob, profile={"tax": {"annual_income": 200000}})
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate", mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} A/C", currency=firm.base_currency, cash_balance=cash)
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=f"X{name[:2].upper()}", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency=firm.base_currency)
                s.add_all([a, i])
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=100, currency=firm.base_currency, source="test", is_real=True))
                s.add(Holding(firm_id=firm.id, account_id=a.id, instrument_id=i.id, quantity=value / 100, market_value=value, cost_basis=value / 2))
                await s.flush()
                return h, p, a

            lee, ann, acct = await client(us, "Lee", date(1966, 6, 1))
            adviser = User(id=uuid.uuid4(), firm_id=us.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            await s.flush()
            ex = ToolExecutors(s, adviser.id, adviser.role, us.id)
            outsider = ToolExecutors(s, adviser.id, adviser.role, nz.id)

            async def card(tool, inputs, executor=ex):
                return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

            print("\n=== Nothing on file ===")
            read = await ex.read_financial_plan({"household_id": str(lee.id)})
            r = read["retirement"]
            pot = 1000000 * 0.7
            check("the income is the engine's own 5% of the funded pot", (r["income_basis"], r["income_target"], r["income_need"]), ("assumed_draw", round(pot * 0.05), round(pot * 0.05)))
            plain = await retirement.for_household(s, lee.id, firm_id=us.id)
            check("and the canvas reads the same", (plain["income_target"], plain["success_probability"]), (r["income_target"], r["success_probability"]))
            check("nothing is counted on", (r["guaranteed_income"], read["streams"]), (0.0, []))
            check("a reserve with no spending is not assessed", (read["cash"]["reserve"]["status"], read["cash"]["reserve"]["months"]), ("not_assessed", None))
            check("the rates are there", (r["withdrawal_rate"] is not None, r["sustainable_rate"] is not None, r["rule_of_thumb_rate"]), (True, True, 0.04))
            check("no debt is assets only", read["cash"]["basis"], "assets_only")
            check("it says it is an estimate", "estimates from what is on file" in read["message"], True)
            await refused("another firm's household", outsider.read_financial_plan({"household_id": str(lee.id)}), "not found")

            print("\n=== Spending and debts ===")
            add = lambda kind, name, amount, **kw: balance_sheet.record_item(s, lee.id, firm_id=us.id, kind=kind, name=name, amount=amount, as_of=today, source_document="Statement Oct 2026", by="a@t", **kw)  # noqa: E731
            await add("essential_spending", "Living", 48000)
            await add("discretionary_spending", "Travel", 12000)
            await add("mortgage", "Home loan", 100000, annual_payment=12000, interest_rate=0.06, ends_on=today + timedelta(days=365 * 30))
            await add("personal_loan", "Car", 10000, annual_payment=6000, interest_rate=0.09, ends_on=today + timedelta(days=500))
            await add("credit_card", "Visa", 5000, interest_rate=0.2)
            read = await ex.read_financial_plan({"household_id": str(lee.id)})
            r = read["retirement"]
            check("recorded spending is the income to plan for, with debts that outlast work", (r["income_basis"], r["income_need"]), ("recorded_spending", 72000))
            cash = read["cash"]
            check("the reserve reads in months of essentials", (cash["reserve"]["status"], cash["reserve"]["months"], cash["reserve"]["target_months"], cash["reserve"]["short_by"]), ("assessed", 12.5, 6, 0.0))
            names = [d["name"] for d in cash["debts"]]
            check("dearest debt first", names, ["Visa", "Car", "Home loan"])
            loan = next(d for d in cash["debts"] if d["name"] == "Home loan")
            near("years to clear", loan["years_to_clear"], 11.9, 0.1)
            near("interest left", loan["interest_left"], 42800, 1500)
            check("a debt with no payment on file is not assessed", next(d for d in cash["debts"] if d["name"] == "Visa")["status"], "not_assessed")
            check("one dearer than the portfolio says so", next(d for d in cash["debts"] if d["name"] == "Visa")["dearer_than_the_portfolio"], True)
            check("a payment under the interest never clears", fp._years_to_clear(1000, 0.2, 100), None)
            p = cash["projection"]
            check("ten years", (len(p), p[0]["year"], p[-1]["year"]), (10, today.year + 1, today.year + 10))
            check("debts fall, invested money grows", (p[-1]["owed"] < p[0]["owed"], p[-1]["invested"] > p[0]["invested"]), (True, True))

            print("\n=== Income to count on ===")
            ss = {"person_id": str(ann.id), "kind": "social_security", "annual_amount": 36000, "start_age": 70, "source_document": "SSA statement 2026"}
            shown = await card("record_income_stream", ss)
            check("the card names it", (shown["Person"], shown["At full retirement age"], shown["Claiming at"]), ("Lee One", "$36,000", "70"))
            await refused("an unknown kind", ex.preview_change("record_income_stream", {**ss, "kind": "lottery"}), "kind must be")
            await refused("no amount", ex.preview_change("record_income_stream", {**ss, "annual_amount": 0}), "above zero")
            await refused("claiming at 75", ex.preview_change("record_income_stream", {**ss, "start_age": 75}), "between 62 and 70")
            await refused("no statement", ex.preview_change("record_income_stream", {**ss, "source_document": "x"}), "which statement")
            await refused("another firm's person", outsider.preview_change("record_income_stream", ss), "not found")
            check("showing it writes nothing", (await s.execute(select(func.count()).select_from(IncomeStream))).scalar(), 0)
            read = await ex.record_income_stream(ss)
            stream = read["streams"][0]
            check("claiming at 70 pays 124%", stream["planned_annual"], 44640.0)
            check("the table is there", [c["age"] for c in stream["claiming"]["claims"]], ["62", "67", "70"])
            r = read["retirement"]
            check("claimed after retirement it is not counted, and says so", (r["guaranteed_income"], r["starts_later"]), (0.0, 44640.0))
            read = await ex.record_income_stream({**ss, "start_age": 62, "annual_amount": 12000, "source_document": "SSA statement, second"})
            r = read["retirement"]
            check("started by retirement it is", r["guaranteed_income"], 8400.0)
            check("and the portfolio draws less", (r["income_need"], r["income_target"]), (72000, 63600))
            check("essentials covered", r["essentials_covered"], 0.175)
            pension = {"person_id": str(ann.id), "kind": "employer_pension", "annual_amount": 24000, "start_age": 65, "source_document": "Pension statement 2026", "lump_sum": 400000, "joint_annual": 20400, "joint_survivor_share_pct": 50}
            shown = await card("record_income_stream", pension)
            check("the card names its choices", ("400,000" in shown["Lump sum"], "20,400" in shown["Joint-life"], shown["Survivor keeps"]), (True, True, "50%"))
            read = await ex.record_income_stream(pension)
            o = next(x for x in read["streams"] if x["kind"] == "employer_pension")["pension_options"]
            check("what its choices imply", (o["years_to_match_lump"], o["cost_of_joint"], o["implied_return"] is not None), (16.7, 0.15, True))
            ended = next(x for x in read["streams"] if x["kind"] == "employer_pension")
            shown = await card("end_income_stream", {"stream_id": ended["id"], "reason": "Taken as a lump sum"})
            check("ending one names it", shown["Income"], "Employer pension")
            await refused("no reason", ex.preview_change("end_income_stream", {"stream_id": ended["id"], "reason": ""}), "say why")
            await refused("another firm's", outsider.preview_change("end_income_stream", {"stream_id": ended["id"], "reason": "x y z"}), "not found")
            read = await ex.end_income_stream({"stream_id": ended["id"], "reason": "Taken as a lump sum"})
            check("it stops counting", any(x["kind"] == "employer_pension" for x in read["streams"]), False)
            await refused("not twice", ex.preview_change("end_income_stream", {"stream_id": ended["id"], "reason": "Again"}), "already ended")

            print("\n=== Education ===")
            school = {"household_id": str(lee.id), "student": "Mia Lee", "start_year": today.year + 5, "years": 4, "annual_cost": 30000, "cost_source": "University published fees 2026"}
            shown = await card("record_education_goal", school)
            check("the card names it", (shown["Client"], shown["For"], shown["Years"], shown["Source"]), ("Lee", "Mia Lee", "4", "University published fees 2026"))
            await refused("no source", ex.preview_change("record_education_goal", {**school, "cost_source": ""}), "where the cost comes from")
            await refused("a year gone", ex.preview_change("record_education_goal", {**school, "start_year": today.year - 1}), "has passed")
            await refused("no cost", ex.preview_change("record_education_goal", {**school, "annual_cost": 0}), "above zero")
            await refused("another firm's household", outsider.preview_change("record_education_goal", school), "not found")
            check("showing it writes nothing", (await s.execute(select(func.count()).select_from(Goal))).scalar(), 0)
            read = await ex.record_education_goal(school)
            e = read["education"][0]
            near("the total carries inflation", e["total_cost"], sum(30000 * 1.05 ** (5 + k) for k in range(4)), 2)
            check("nothing saved is nothing funded", (e["saved"], e["funded"]), (0, 0.0))
            check("and what to save a year is there", e["saving_a_year"] > 0, True)
            await refused("one student once", ex.preview_change("record_education_goal", school), "already on file")
            s.add(Goal(id=uuid.uuid4(), firm_id=us.id, household_id=lee.id, name="School", kind="education", target_amount=1000, priority=2, assumptions={}))
            await s.flush()
            read = await ex.read_financial_plan({"household_id": str(lee.id)})
            check("a goal with no cost on file is not assessed", next(g for g in read["education"] if g["goal"] == "School")["status"], "not_assessed")

            print("\n=== Outside the US ===")
            kiwi, tui, _ = await client(nz, "Kim", date(1966, 6, 1))
            nx = ToolExecutors(s, adviser.id, UserRole.ADVISER, nz.id)
            read = await nx.record_income_stream({"person_id": str(tui.id), "kind": "state_pension", "annual_amount": 30000, "start_age": 65, "source_document": "NZ Super statement"})
            check("no claiming table", read["streams"][0]["claiming"], None)
            sheet = await balance_sheet.record_item(s, kiwi.id, firm_id=nz.id, kind="essential_spending", name="Living", amount=120000, as_of=today, source_document="Budget 2026", by="a@t")
            read = await nx.read_financial_plan({"household_id": str(kiwi.id)})
            check("the firm's reserve target is read", read["cash"]["reserve"]["target_months"], 3)
            check("and what falls short of it", read["cash"]["reserve"]["short_by"], 0.0 if read["cash"]["reserve"]["cash"] >= 30000 else None)

            print("\n=== Someone who has died ===")
            ann.deceased_on = today
            await s.flush()
            read = await ex.read_financial_plan({"household_id": str(lee.id)})
            check("their income is not counted", (read["streams"], read["people"]), ([], []))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
