"""Prove the performance engine (L100 §9): a real time-weighted return rebuilt from the
ledger, a money-weighted return, and attribution that adds up to the headline.

The case that matters is the second one. Two instruments, one bought after a fall: the old
firm figure compared today's holdings at the earliest price and reported 0.0%; the investor
who bought low actually made 12.5% time-weighted. Every expected value below is worked by hand.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_performance
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import performance
from app.core.db import Base
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import (
    Holding, Instrument, ModelPortfolio, Price, TaxLot, Transaction,
)
from app.models.tenant import Firm

PASS, FAIL = [], []
D1, D2, D3 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)


def check(label: str, got, want, tol: float = 0.0001) -> None:
    ok = (abs(float(got) - float(want)) < tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def run() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "household", "person", "legal_entity", "mandate", "account", "instrument",
              "price", "holding", "txn", "tax_lot", "model_portfolio"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    try:
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
            s.add(firm)
            await s.flush()

            async def instrument(symbol, asset_class, closes, real=True):
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol,
                                  asset_class=asset_class)
                s.add(inst)
                await s.flush()
                for d, px in closes:
                    s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=d, close=px,
                                currency="USD", source="test", is_real=real))
                await s.flush()
                return inst

            async def account(name, mandate=None):
                if mandate is None:
                    p = Person(id=uuid.uuid4(), firm_id=firm.id, full_name=name)
                    s.add(p)
                    await s.flush()
                    mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id,
                                      name=name, mandate_type="advisory")
                    s.add(mandate)
                    await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name=name,
                            currency="USD", cash_balance=Decimal("0"))
                s.add(a)
                await s.flush()
                return a

            async def hold(acct, inst, qty, mv):
                h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id,
                            instrument_id=inst.id, quantity=Decimal(str(qty)),
                            market_value=Decimal(str(mv)), cost_basis=Decimal(str(mv)))
                s.add(h)
                await s.flush()
                return h

            def txn(acct, inst, kind, qty, price, d):
                s.add(Transaction(firm_id=firm.id, account_id=acct.id, instrument_id=inst.id,
                                  txn_type=kind, quantity=qty, price=price,
                                  amount=qty * price, trade_date=d))

            def dividend(acct, inst, amount, d):
                s.add(Transaction(firm_id=firm.id, account_id=acct.id, instrument_id=inst.id,
                                  txn_type="dividend", quantity=0, price=0, amount=amount, trade_date=d))

            print("\n=== One instrument, no trades: 1000 -> 1100 -> 990 ===")
            a1 = await account("Plain")
            i1 = await instrument("P1", "equity", [(D1, 100), (D2, 110), (D3, 99)])
            h1 = await hold(a1, i1, 10, 990)
            s.add(TaxLot(firm_id=firm.id, holding_id=h1.id, quantity=10, cost_per_unit=100,
                         acquired_on=date(2025, 1, 1)))
            await s.flush()
            r = await performance.account_performance(s, a1.id, firm_id=firm.id)
            check("two monthly periods", r["months"], 2)
            check("TWR links +10% then -10% to -1%, not their sum", r["twr"], -0.01)
            check("with no flows the money-weighted return equals it", r["mwr"]["period"], -0.01)
            check("no flows: first month return", r["periods"][0]["return"], 0.1)
            check("window ends at the last price on file", r["end"], D3.isoformat())
            check("the price age is disclosed", r["disclosures"]["stale_days"] >= 0, True)
            check("cash and fees are disclosed as out of scope",
                  (r["disclosures"]["scope"], r["disclosures"]["gross_of_fees"],
                   r["disclosures"]["external_cash_flows_recorded"]),
                  ("securities_only", True, False))

            print("\n=== Bought after a fall: the old method says 0.0%, the right answer is 12.5% ===")
            a2 = await account("Bought low")
            fi = await instrument("FI", "fixed_income", [(D1, 100), (D2, 100), (D3, 100)])
            eq = await instrument("EQ", "equity", [(D1, 100), (D2, 50), (D3, 100)])
            await hold(a2, fi, 10, 1000)
            await hold(a2, eq, 20, 2000)          # 10 held at the start + 10 bought on Feb 1
            txn(a2, eq, "buy", 10, 50, D2)
            await s.flush()
            r = await performance.account_performance(s, a2.id, firm_id=firm.id)
            # Month 1: V 2000 -> 2000, flow 500 in, so (2000-2000-500)/2000 = -25%.
            # Month 2: 2000 -> 3000 = +50%.  1.5 * 0.75 - 1 = 12.5%.
            # The old figure: today's 10 FI + 20 EQ at the Jan 1 prices = 3000, vs 3000 now = 0.0%.
            check("month 1 is -25%", r["periods"][0]["return"], -0.25)
            check("month 2 is +50%", r["periods"][1]["return"], 0.5)
            check("TWR is 12.5%", r["twr"], 0.125)
            check("the buy is a flow into the sleeve, not a gain", r["periods"][0]["net_flow"], 500)
            g = 1 + r["mwr"]["period"]
            residual = 2000 + 500 * g ** (-31 / 59) - 3000 / g
            check("money-weighted return is a root of the dated cash flows", residual, 0, tol=0.5)
            check("buying before the rally makes MWR beat TWR", r["mwr"]["period"] > r["twr"], True)
            contrib = {a["asset_class"]: a["contribution"] for a in r["attribution"]}
            check("equity contributed 12.5 points", contrib["equity"], 0.125)
            check("fixed income contributed nothing", contrib["fixed_income"], 0.0)
            check("attribution adds up to the headline", sum(contrib.values()), r["twr"])

            print("\n=== Income counts as return, not as a flow ===")
            a3 = await account("Income")
            i3 = await instrument("DIV", "equity", [(D1, 100), (D2, 100), (D3, 100)])
            await hold(a3, i3, 10, 1000)
            dividend(a3, i3, 100, date(2026, 2, 15))
            await s.flush()
            r = await performance.account_performance(s, a3.id, firm_id=firm.id)
            check("flat price, 100 dividend on 1000 = 10%", r["twr"], 0.10)
            check("income is reported on its month", r["periods"][1]["income"], 100)

            print("\n=== A purchase the ledger never recorded is dated by its tax lot, and disclosed ===")
            a4 = await account("Gap")
            i4 = await instrument("GAP", "equity", [(D1, 100), (D2, 100), (D3, 110)])
            h4 = await hold(a4, i4, 15, 1650)
            s.add(TaxLot(firm_id=firm.id, holding_id=h4.id, quantity=10, cost_per_unit=100,
                         acquired_on=date(2025, 6, 1)))
            s.add(TaxLot(firm_id=firm.id, holding_id=h4.id, quantity=5, cost_per_unit=100,
                         acquired_on=date(2026, 2, 10)))
            await s.flush()
            r = await performance.account_performance(s, a4.id, firm_id=firm.id)
            # Month 2: V 1000 -> 1650, 500 bought Feb 10 (19 of 28 days left):
            # (1650-1000-500) / (1000 + 500*19/28) = 150 / 1339.2857 = 11.2%.
            # Treating all 15 shares as held all along would give 10.0%.
            check("the unrecorded lot is treated as a flow", r["twr"], 150 / (1000 + 500 * 19 / 28), tol=0.0002)
            check("the missing ledger entry is disclosed", r["disclosures"]["ledger_gap_lots"], 1)

            print("\n=== A position sold out inside the window still counts ===")
            a5 = await account("Sold out")
            i5 = await instrument("SOLD", "equity", [(D1, 100), (D2, 110), (D3, 121)])
            await hold(a5, i5, 0, 0)
            txn(a5, i5, "sell", 10, 110, D2)
            await s.flush()
            r = await performance.account_performance(s, a5.id, firm_id=firm.id)
            check("the month it was held is +10%", r["periods"][0]["return"], 0.1)
            check("the month with no capital is skipped, not scored", r["disclosures"]["periods_skipped"], 1)
            check("TWR is the held month alone", r["twr"], 0.1)

            print("\n=== Synthetic and unpriced holdings are named, never hidden ===")
            a6 = await account("Disclosures")
            real = await instrument("REAL", "equity", [(D1, 100), (D2, 100), (D3, 100)])
            fake = await instrument("FAKE", "alternatives", [(D1, 10), (D2, 10), (D3, 10)], real=False)
            priv = await instrument("PRIV", "alternatives", [(D3, 1000)])
            await hold(a6, real, 10, 1000)
            await hold(a6, fake, 100, 1000)
            await hold(a6, priv, 1, 1000)
            await s.flush()
            r = await performance.account_performance(s, a6.id, firm_id=firm.id)
            check("a synthetic price is named", r["disclosures"]["synthetic_price_symbols"], ["FAKE"])
            check("an instrument with no price history is named", r["disclosures"]["unpriced_flat_symbols"], ["PRIV"])
            check("flat holdings give a flat return", r["twr"], 0.0)

            print("\n=== Household scope, firm scope and the benchmark window ===")
            bench = await instrument("BENCH", "equity", [(D1, 100), (D2, 105), (D3, 110)])
            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Core", drift_band=0.05,
                                   benchmark_symbol="BENCH")
            s.add(model)
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="HH")
            s.add(hh)
            await s.flush()
            owner = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Owner")
            s.add(owner)
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=owner.id, name="HH mandate",
                              mandate_type="advisory", model_portfolio_id=model.id)
            s.add(mandate)
            await s.flush()
            ha = await account("HH account", mandate)
            hi = await instrument("HHI", "equity", [(D1, 100), (D2, 110), (D3, 121)])
            await hold(ha, hi, 10, 1210)
            await s.flush()
            r = await performance.household_performance(s, hh.id, firm_id=firm.id)
            check("household TWR is +10% then +10% = 21%", r["twr"], 0.21)
            check("benchmark is measured over the same window", r["benchmark_return"], 0.10)
            check("alpha is portfolio minus benchmark", r["alpha"], 0.11)
            check("another firm cannot read this household",
                  await performance.household_performance(s, hh.id, firm_id=uuid.uuid4()), None)
            check("a household with no accounts has no return, not zero",
                  (await performance.performance_for_accounts(s, [], firm_id=firm.id))["twr"], None)
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit


def main() -> int:
    asyncio.run(run())
    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
