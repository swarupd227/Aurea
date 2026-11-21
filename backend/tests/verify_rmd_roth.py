"""RMDs follow the rule (B-170) and Roth conversion room is the household's, in its tax year, laid out (B-171).

    .venv\\Scripts\\python.exe -m tests.verify_rmd_roth

B-170: the divisor is the Uniform Lifetime Table to 120 and over, read at the age reached this year; the start age
comes from the birth year (73 for 1951-1959, 75 from 1960); the balance is the account's value at the previous
31 December, at that day's real prices, as the book stood then, and an account not valued that day is not estimated;
an owner's IRAs aggregate. B-171: a couple filing jointly has one return and one bracket's room, the filing status is
recorded or named as assumed, a year with no brackets is not assessed, and the result lays out room, not a
recommendation.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import cash_movement, firm_time, registration, rmd, tax_intelligence as ti, transfer_file
from app.core.db import Base
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []
NOON_20_NOV = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)   # 12:00 in New York


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


@contextmanager
def clock(moment: datetime):
    real = firm_time.utcnow
    firm_time.utcnow = lambda: moment
    try:
        yield
    finally:
        firm_time.utcnow = real


def rules() -> None:
    print("\n=== The table, to 120 and over (B-170) ===")
    check("72 reads 27.4", rmd.divisor(72), 27.4)
    check("95 reads 8.9, not the 12.2 it stopped at", rmd.divisor(95), 8.9)
    check("120 and over read 2.0", (rmd.divisor(120), rmd.divisor(124)), (2.0, 2.0))
    check("every age from 72 to 119 has a divisor, falling", all(rmd.divisor(a) > rmd.divisor(a + 1) for a in range(72, 119)), True)
    print("\n=== The start age is the birth year's ===")
    check("born 1959: 73", rmd.start_age(date(1959, 6, 1)), 73)
    check("born 1960: 75", rmd.start_age(date(1960, 1, 1)), 75)
    check("the rollover file reads the same rule", transfer_file.rmd_age(date(1961, 3, 3)), 75)
    check("the table is read at the age reached this year", rmd.age_in_year(date(1953, 12, 31), 2026), 73)


def tax_view() -> None:
    today = date(2026, 11, 20)
    print("\n=== The tax view reads the same rule ===")
    brain = {"persons": [
        {"full_name": "Ida Young", "date_of_birth": "1961-03-03", "profile": {"tax": {"ira_value": 400_000}}},
        {"full_name": "Ned Old", "date_of_birth": "1931-02-02", "profile": {"tax": {"ira_value": 89_000}}},
    ]}
    out = ti._us_rmd_planning(brain, today=today)
    ida = next(a for a in out["alerts"] if a["person_name"] == "Ida Young")
    ned = next(a for a in out["alerts"] if a["person_name"] == "Ned Old")
    check("born 1961: 65 this year, RMDs start at 75 in ten years, not 73", (ida["age"], ida["starts_at_age"], ida["years_to_rmd"]), (65, 75, 10))
    check("and it no longer says 73", "73" in ida["action"], False)
    check("95 this year: divisor 8.9, so 10,000, not 7,295", (ned["life_expectancy_factor"], ned["estimated_rmd"]), (8.9, 10_000.0))
    check("the balance is named as the profile's, not a year-end value", ned["balance_source"], "profile")
    check("it cites the table", any("Table III" in l["source"] for l in out["law"]), True)

    print("\n=== Roth room is the return's, in its year, laid out (B-171) ===")
    couple = {"household": {"values": {}}, "persons": [
        {"full_name": "Ann Lee", "profile": {"tax": {"annual_income": 100_000, "ira_value": 300_000}}},
        {"full_name": "Bo Lee", "profile": {"tax": {"annual_income": 100_000, "ira_value": 200_000}}},
    ]}
    r = ti._us_roth_conversion(couple, today=today)
    check("one joint return, not two adults each against the joint brackets", r["count"], 1)
    row = r["candidates"][0]
    check("income counted once: 200,000 is in 2026's 22% band, 11,400 below its 211,400 top",
          (row["taxable_income"], row["marginal_rate_pct"], row["bracket_top"], row["room_to_bracket_top"]), (200_000, 22.0, 211_400, 11_400))
    check("both IRAs on the return", row["ira_balance"], 500_000)
    check("the filing status is named as assumed", (r["filing_status"], r["filing_status_recorded"], "filing_status" in r["assumed"]),
          ("married_filing_jointly", False, True))
    check("income is named as the profile's, no return on file", "taxable_income" in r["assumed"], True)
    check("laid out: no recommended amount, no 'opportunity'",
          ("recommended_conversion" in row, "attractive" in row, "opportunit" in row["action"].lower() or "consider" in row["action"].lower()),
          (False, False, False))
    singles = {**couple, "household": {"values": {"filing_status": "single"}}}
    r2 = ti._us_roth_conversion(singles, today=today)
    check("recorded single: a return each on the single brackets (100,000 is 22%, 5,700 to 105,700)",
          (r2["count"], [c["room_to_bracket_top"] for c in r2["candidates"]], r2["filing_status_recorded"]), (2, [5_700, 5_700], True))
    hoh = ti._us_roth_conversion({**couple, "household": {"values": {"filing_status": "head_of_household"}}}, today=today)
    check("a status with no brackets on file is not assessed", (hoh["status"], hoh["count"]), ("not_assessed", 0))
    later = ti._us_roth_conversion(couple, today=date(2028, 6, 1))
    check("a year with no brackets on file is not assessed, not last year's", (later["status"], "2028" in later["reason"]), ("not_assessed", True))
    check("2027 reads 2027's brackets", ti._us_roth_conversion(couple, today=date(2027, 3, 1))["candidates"][0]["bracket_top"], 218_250)


async def accounts() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Hudson", slug="hudson", jurisdiction="US", base_currency="USD")
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Old", values={})
            s.add_all([firm, hh])
            await s.flush()
            ned = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Ned Old", date_of_birth=date(1931, 2, 2))
            ida = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Ida Young", date_of_birth=date(1961, 3, 3))
            s.add_all([ned, ida])
            await s.flush()
            m_ned = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ned.id, name="Ned", mandate_type="advisory")
            m_ida = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ida.id, name="Ida", mandate_type="advisory")
            s.add_all([m_ned, m_ida])
            await s.flush()
            eq = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="EQ", name="Equity", asset_class="equity", currency="USD")
            odd = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="ODD", name="Unpriced", asset_class="equity", currency="USD")
            s.add_all([eq, odd])
            await s.flush()
            # 31 Dec 2025 at 100, today at 130: the RMD is worked from the year-end value.
            s.add_all([Price(id=uuid.uuid4(), firm_id=firm.id, instrument_id=eq.id, as_of=date(2025, 12, 31), close=Decimal("100"), is_real=True),
                       Price(id=uuid.uuid4(), firm_id=firm.id, instrument_id=eq.id, as_of=date(2026, 11, 19), close=Decimal("130"), is_real=True)])
            ira1 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m_ned.id, name="Ned IRA", currency="USD", cash_balance=0,
                           registration_type="traditional_ira", status="active")
            ira2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m_ned.id, name="Ned Rollover IRA", currency="USD", cash_balance=0,
                           registration_type="employer_rollover", status="active")
            sep = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m_ida.id, name="Ida SEP", currency="USD", cash_balance=0,
                          registration_type="sep_ira", status="active")
            s.add_all([ira1, ira2, sep])
            await s.flush()
            # Ned's IRA holds 1,000 today, 890 at year end: 110 were bought on 5 Mar 2026.
            s.add_all([Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=ira1.id, instrument_id=eq.id, quantity=Decimal("1000"),
                               market_value=Decimal("130000"), cost_basis=Decimal("90000")),
                       Transaction(id=uuid.uuid4(), firm_id=firm.id, account_id=ira1.id, instrument_id=eq.id, txn_type="buy",
                                   quantity=Decimal("110"), price=Decimal("110"), amount=Decimal("0"), trade_date=date(2026, 3, 5)),
                       Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=ira2.id, instrument_id=eq.id, quantity=Decimal("178"),
                               market_value=Decimal("23140"), cost_basis=Decimal("15000"))])
            await s.flush()

            with clock(NOON_20_NOV):
                due = await registration.rmd_for_account(s, ira1)
                print("\n=== An IRA's RMD is its 31 December value over the table's divisor (B-170) ===")
                check("95 this year: required, divisor 8.9", (due["status"], due["owner_age"], due["divisor"]), ("required", 95, 8.9))
                check("valued at 31 Dec 2025: 890 units at 100, not today's 1,000 at 130", (due["year_end"], due["account_value"]), ("2025-12-31", 89_000.0))
                check("so 10,000, where today's holdings over 12.2 read 10,655.74", due["amount"], 10_000.0)
                check("it names who owns it, for the total", due["owner_name"], "Ned Old")

                young = await registration.rmd_for_account(s, sep)
                check("a SEP IRA owned by someone born 1961 starts at 75", (young["status"], young["owner_age"], young["starts_at_age"]), ("not_yet", 65, 75))

                # The rollover IRA also holds something with no real price on 31 Dec.
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=ira2.id, instrument_id=odd.id, quantity=Decimal("5"),
                              market_value=Decimal("500"), cost_basis=Decimal("500")))
                await s.flush()
                gap = await registration.rmd_for_account(s, ira2)
                check("an IRA not valued at 31 Dec is not estimated", (gap["status"], gap["amount"], "ODD" in gap["reason"]), ("not_valued", None, True))

                rows = await cash_movement._rmd_rows(s, [ira1, ira2, sep], date(2026, 11, 20))
                check("one row per owner, the IRAs together", (len(rows), rows[0]["owner"], rows[0]["aggregated"]), (1, "Ned Old", True))
                check("an unvalued IRA leaves the total unknown, not short", (rows[0]["required"], rows[0]["left"], rows[0]["not_valued"]),
                      (None, None, ["Ned Rollover IRA"]))

                await s.delete((await s.execute(__import__("sqlalchemy").select(Holding).where(Holding.instrument_id == odd.id))).scalars().one())
                s.add(Transaction(id=uuid.uuid4(), firm_id=firm.id, account_id=ira2.id, txn_type="withdrawal", amount=Decimal("6000"),
                                  trade_date=date(2026, 6, 1), lineage={"kind": "rmd"}))
                await s.flush()
                rows = await cash_movement._rmd_rows(s, [ira1, ira2, sep], date(2026, 11, 20))
                # The rollover's book at 31 Dec: 178 units, and 6,000 cash that left in June -> 17,800 + 6,000 = 23,800; 23,800 / 8.9.
                rollover = round(23_800 / 8.9, 2)
                check("valued again, the owner's total is both IRAs'", rows[0]["required"], round(10_000.0 + rollover, 2))
                check("what was taken from the rollover counts against the total", (rows[0]["taken"], rows[0]["left"]),
                      (6_000.0, round(10_000.0 + rollover - 6_000, 2)))
    finally:
        await db.dispose()


def main() -> int:
    rules()
    tax_view()
    asyncio.run(accounts())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
