"""Prove the billing calculation (L100 §9, L200 Track A step 2): the right period, the
right proration, marginal bands on the right balance, householding that shares one fee
across a household without losing a cent, and the checks that stop an unsafe bill.

Every expected figure is worked by hand in the comment beside it. Runs against a throwaway
SQLite database so it needs no cloud connection.

    python -m tests.verify_billing
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import billing
from app.core.db import Base
from app.models.billing import FeeArrangement
from app.models.graph import Account, Household, Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
AS_OF = date(2026, 10, 5)            # Q3 2026 is the quarter just ended
PRICE_DATE = date(2026, 9, 30)       # 5 days before AS_OF
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def check(label: str, got, want, tol: float = 0.005) -> None:
    ok = (abs(float(got) - float(want)) < tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def codes(entry) -> set:
    return {c["code"] for c in entry["checks"]}


async def run() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "household", "person", "legal_entity", "mandate", "account", "instrument",
              "price", "holding", "fee_schedule", "fee_arrangement"]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[Base.metadata.tables[n] for n in wanted]))
    Session = async_sessionmaker(db, expire_on_commit=False)

    try:
        print("\n=== Which period a bill covers ===")
        check("monthly in arrears bills last month", billing.billing_period("monthly", "arrears", AS_OF),
              (date(2026, 9, 1), date(2026, 9, 30)))
        check("quarterly in arrears bills last quarter", billing.billing_period("quarterly", "arrears", AS_OF),
              (date(2026, 7, 1), date(2026, 9, 30)))
        check("annual in arrears bills last year", billing.billing_period("annually", "arrears", AS_OF),
              (date(2025, 1, 1), date(2025, 12, 31)))
        check("quarterly in advance bills the quarter just starting", billing.billing_period("quarterly", "advance", AS_OF),
              (date(2026, 10, 1), date(2026, 12, 31)))
        check("arrears across a year boundary", billing.billing_period("monthly", "arrears", date(2026, 1, 15)),
              (date(2025, 12, 1), date(2025, 12, 31)))
        check("a leap-year February ends on the 29th", billing.billing_period("monthly", "arrears", date(2028, 3, 3)),
              (date(2028, 2, 1), date(2028, 2, 29)))

        print("\n=== The share of a period an arrangement was in force ===")
        q3 = (date(2026, 7, 1), date(2026, 9, 30))   # 92 days
        check("in force all quarter", billing.period_fraction(date(2026, 1, 1), None, *q3), 1.0)
        check("from 15 Aug: 47 of 92 days", billing.period_fraction(date(2026, 8, 15), None, *q3), 47 / 92, tol=1e-9)
        check("ended 15 Aug: 46 of 92 days", billing.period_fraction(date(2026, 1, 1), date(2026, 8, 15), *q3), 46 / 92, tol=1e-9)
        check("starting after the period: nothing", billing.period_fraction(date(2026, 10, 1), None, *q3), 0.0)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Test", slug="test", base_currency="NZD")
            s.add(firm)
            await s.flush()

            tiered = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="T", name="Tiered", fee_type="tiered_bps",
                                 tiers=[{"min_aum": 0, "max_aum": 1_000_000, "bps": 100},
                                        {"min_aum": 1_000_000, "max_aum": 5_000_000, "bps": 75}],
                                 minimum_annual_fee=Decimal("3000"), currency="NZD")
            flat_fee = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="R", name="Retainer", fee_type="flat_fee",
                                   flat_fee=Decimal("18000"), tiers=[], currency="NZD")
            flat_bps = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="B", name="Flat 100bps", fee_type="flat_bps",
                                   flat_bps=Decimal("100"), tiers=[], currency="NZD")
            s.add_all([tiered, flat_fee, flat_bps])
            await s.flush()

            async def instrument(sym, price_date=PRICE_DATE, real=True, priced=True):
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class="equity")
                s.add(inst)
                await s.flush()
                if priced:
                    s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=price_date, close=10,
                                currency="NZD", source="test", is_real=real))
                    await s.flush()
                return inst

            base_inst = await instrument("BASE")

            async def household(name):
                hh = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(hh)
                await s.flush()
                return hh

            async def mandate(hh, name, value, *, schedule=tiered, cash=0, inst=None, currency="NZD",
                              effective=date(2026, 1, 1), householding=False, confirmed=True, arrange=True,
                              frequency="quarterly", method="arrears"):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name=name)
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=name,
                            mandate_type="advisory", is_active=True)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} account",
                            currency=currency, cash_balance=Decimal(str(cash)))
                s.add(a)
                await s.flush()
                inst = inst or base_inst
                if value - cash > 0:
                    s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=inst.id,
                                  quantity=Decimal("1"), market_value=Decimal(str(value - cash)),
                                  cost_basis=Decimal("0")))
                await s.flush()
                if arrange:
                    await billing.activate_arrangement(
                        s, firm_id=firm.id, mandate=m, fee_schedule_id=schedule.id, billing_method=method,
                        billing_frequency=frequency, householding=householding, effective_from=effective,
                        set_by="maker@x", set_at=NOW,
                        confirmed_by="checker@x" if confirmed else None, confirmed_at=NOW if confirmed else None,
                        source="seed")
                return m

            def by_name(result, name):
                return next(e for e in result["arrangements"] if e["mandate_name"] == name)

            # --- scenarios, each in its own household so none touches another -------------------
            await mandate(await household("H solo"), "Solo", 2_000_000, cash=100_000)
            hh_group = await household("H group")
            await mandate(hh_group, "Group A", 600_000, householding=True)
            await mandate(hh_group, "Group B", 1_400_000, householding=True)
            hh_apart = await household("H apart")
            await mandate(hh_apart, "Apart A", 600_000)
            await mandate(hh_apart, "Apart B", 1_400_000)
            await mandate(await household("H prorated"), "Prorated", 1_000_000, effective=date(2026, 8, 15))
            await mandate(await household("H minimum"), "Minimum", 100_000)
            await mandate(await household("H retainer"), "Retainer", 500_000, schedule=flat_fee)
            hh_round = await household("H rounding")
            await mandate(hh_round, "Round 1", 1_000.01, schedule=flat_bps, householding=True)
            await mandate(hh_round, "Round 2", 2_000.03, schedule=flat_bps, householding=True)
            await mandate(hh_round, "Round 3", 3_000.07, schedule=flat_bps, householding=True)

            stale = await instrument("STALE", price_date=PRICE_DATE - timedelta(days=60))
            fake = await instrument("FAKE", real=False)
            priv = await instrument("PRIV", priced=False)
            await mandate(await household("H stale"), "Stale", 1_000_000, inst=stale)
            await mandate(await household("H synthetic"), "Synthetic", 1_000_000, inst=fake)
            await mandate(await household("H unpriced"), "Unpriced", 1_000_000, inst=priv)
            await mandate(await household("H usd"), "Usd", 1_000_000, currency="USD")
            await mandate(await household("H empty"), "Empty", 0)
            await mandate(await household("H future"), "Future", 1_000_000, effective=date(2026, 10, 1))
            await mandate(await household("H unagreed"), "Unagreed", 400_000, arrange=False)
            await mandate(await household("H pending"), "Pending", 300_000, confirmed=False)
            await s.flush()

            r = await billing.preview(s, firm.id, as_of=AS_OF)

            print("\n=== Marginal bands on the whole balance, cash included ===")
            e = by_name(r, "Solo")
            # 2,000,000 = 1,000,000 cash+holdings. 1M @100bps = 10,000; 1M @75bps = 7,500; 17,500 a year.
            check("billable value is cash plus holdings", e["billable_value"], 2_000_000)
            check("annual fee is 10,000 + 7,500", e["annual_fee"], 17_500)
            check("a quarter is a quarter of it", e["amount"], 4_375)
            check("effective rate is 87.5 bps", e["effective_bps"], 87.5)
            check("it covers Q3 2026", (e["period_start"], e["period_end"]), ("2026-07-01", "2026-09-30"))
            check("no checks raised on a clean bill", e["checks"], [])
            check("the bill's working is kept", len(e["breakdown"]), 2)

            print("\n=== Householding: one fee on the combined balance, shared by value ===")
            a, b = by_name(r, "Group A"), by_name(r, "Group B")
            # 2,000,000 combined -> 17,500 a year -> 4,375 a quarter, split 30% / 70%.
            check("the breakpoint is reached on the combined balance", a["group_value"], 2_000_000)
            check("A pays 30% of the quarter", a["amount"], 1_312.50)
            check("B pays 70% of the quarter", b["amount"], 3_062.50)
            check("together they pay exactly the household fee", a["amount"] + b["amount"], 4_375)
            apart = by_name(r, "Apart A")["amount"] + by_name(r, "Apart B")["amount"]
            # Separately: 600k -> 6,000 -> 1,500; 1.4M -> 10,000 + 3,000 = 13,000 -> 3,250.  Total 4,750.
            check("billed separately the same accounts would pay 4,750", apart, 4_750)
            check("householding saves 375 a quarter", apart - (a["amount"] + b["amount"]), 375)

            print("\n=== A part-period is billed for the part ===")
            e = by_name(r, "Prorated")
            # 1,000,000 -> 10,000 a year -> 2,500 a quarter, x 47/92 = 1,277.17
            check("in force 47 of 92 days", e["period_fraction"], 47 / 92, tol=1e-6)
            check("the quarter is prorated", e["amount"], 1_277.17)

            print("\n=== Minimum and flat fees ===")
            e = by_name(r, "Minimum")
            # 100,000 @ 1% = 1,000, below the 3,000 minimum -> 3,000 a year -> 750 a quarter (300 bps).
            check("the minimum raises the year to 3,000", e["annual_fee"], 3_000)
            check("a quarter of the minimum", e["amount"], 750)
            check("the minimum is flagged for a reviewer", "minimum_fee_applied" in codes(e), True)
            check("an informational check does not block", e["blocked"], False)
            e = by_name(r, "Retainer")
            check("a flat retainer is 18,000 a year", e["annual_fee"], 18_000)
            check("and 4,500 a quarter", e["amount"], 4_500)

            print("\n=== Rounding never loses or invents a cent across a household ===")
            rounds = [by_name(r, f"Round {i}") for i in (1, 2, 3)]
            # 6,000.11 @ 100bps = 60.0011 a year -> 15.000275 a quarter -> 15.00 shared three ways.
            check("the three shares add to the rounded household fee", sum(x["amount"] for x in rounds), 15.00)

            print("\n=== Checks that make a bill unsafe, or worth a second look ===")
            e = by_name(r, "Stale")
            check("a 65-day-old price blocks the bill", ("stale_valuation" in codes(e), e["blocked"]), (True, True))
            check("the staleness is stated: 65 days", e["valuation"]["stale_days"] == 65, True)
            e = by_name(r, "Synthetic")
            check("a synthetic price is flagged but does not block", ("synthetic_price" in codes(e), e["blocked"]), (True, False))
            check("and is named", e["valuation"]["synthetic_price_symbols"], ["FAKE"])
            e = by_name(r, "Unpriced")
            check("a holding with no price is flagged", "unpriced_holdings" in codes(e), True)
            e = by_name(r, "Usd")
            check("an account in another currency blocks, with no conversion", ("currency_mismatch" in codes(e), e["blocked"]), (True, True))
            check("a blocked bill has no amount", e["amount"], 0)
            e = by_name(r, "Empty")
            check("an empty account blocks", "no_billable_value" in codes(e), True)
            e = by_name(r, "Future")
            check("an arrangement not yet in force blocks", "not_in_force" in codes(e), True)
            r_late = await billing.preview(s, firm.id, as_of=date(2026, 10, 30))
            check("a snapshot taken a month after the period end is flagged",
                  "valuation_not_at_period_boundary" in codes(by_name(r_late, "Solo")), True)

            print("\n=== Who has no agreement on file ===")
            names = {x["mandate_name"]: x for x in r["no_agreement"]}
            check("a mandate with no arrangement is listed", "Unagreed" in names, True)
            check("one awaiting confirmation is listed as pending", names["Pending"]["pending_confirmation"], True)
            check("an unconfirmed arrangement is never billed", "Pending" not in {e["mandate_name"] for e in r["arrangements"]}, True)
            check("a confirmed one is not listed", "Solo" in names, False)

            print("\n=== Totals leave out anything blocked ===")
            billed = sum(e["amount"] for e in r["arrangements"] if not e["blocked"])
            check("the total is the sum of unblocked bills", r["totals"]["amount_by_currency"]["NZD"], billed)
            check("blocked bills are counted", r["totals"]["blocked"], 4)  # stale, usd, empty, future

            print("\n=== Changing terms ends the old arrangement, and scope holds ===")
            m = await s.get(Mandate, uuid.UUID(by_name(r, "Solo")["mandate_id"]))
            new = await billing.activate_arrangement(
                s, firm_id=firm.id, mandate=m, fee_schedule_id=flat_fee.id, billing_method="arrears",
                billing_frequency="monthly", householding=False, effective_from=date(2026, 10, 1),
                set_by="a@x", set_at=NOW, confirmed_by="b@x", confirmed_at=NOW)
            from sqlalchemy import select
            rows = (await s.execute(select(FeeArrangement).where(FeeArrangement.mandate_id == m.id))).scalars().all()
            check("only one arrangement is active", sum(1 for x in rows if x.status == "active"), 1)
            check("the old one ended the day before", next(x for x in rows if x.status == "ended").ended_on, date(2026, 9, 30))
            check("the mandate follows the new schedule", m.fee_schedule_id, flat_fee.id)
            try:
                await billing.activate_arrangement(
                    s, firm_id=firm.id, mandate=m, fee_schedule_id=flat_fee.id, billing_method="whenever",
                    billing_frequency="monthly", householding=False, effective_from=AS_OF,
                    set_by=None, set_at=None, confirmed_by=None, confirmed_at=None)
                check("an invalid billing method is refused", "accepted", "refused")
            except ValueError:
                check("an invalid billing method is refused", "refused", "refused")
            other = await billing.preview(s, uuid.uuid4(), as_of=AS_OF)
            check("another firm sees none of it", (len(other["arrangements"]), len(other["no_agreement"])), (0, 0))
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit


def main() -> int:
    asyncio.run(run())
    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
