"""Prove a direct order answers to what a proposal answers to: what the account can do, what the mandate
excludes, the client's IPS and the household's tax window. Refused before its Confirm card and again when
it is confirmed, the set as a whole, and nothing written by being refused.

Runs against a throwaway SQLite database.

    python -m tests.verify_direct_orders
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import SYSTEM_PROMPT_TEMPLATE
from app.models.corporate_actions import CorporateAction
from app.models.enums import LotRelief, MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, InvestmentPolicy, IpsException, Price, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:150]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        async with Session() as s:
            async def firm_of(slug, jurisdiction, currency, **kw):
                f = Firm(id=uuid.uuid4(), name=slug, slug=slug, base_currency=currency, jurisdiction=jurisdiction, **kw)
                s.add(f)
                await s.flush()
                return f

            async def client(firm, name, *, cash=20000, values=None, constraints=None):
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name, values=values or {})
                s.add(h)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=name)
                s.add(p)
                await s.flush()
                return h, await mandate_of(firm, p, name, cash=cash, constraints=constraints)

            async def mandate_of(firm, person, name, *, cash=20000, constraints=None):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name=f"{name} mandate", mandate_type="advisory",
                            suitability={"risk_profile": "growth", "capacity_for_loss": "high"}, constraints=constraints or {})
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} A/C", currency=firm.base_currency, cash_balance=cash)
                s.add(a)
                await s.flush()
                return m, a

            async def instrument(firm, symbol, cls="equity", price=10.0, *, real=True, flags=None):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class=cls, market_type=MarketType.PUBLIC,
                               currency=firm.base_currency, values_tags={"flags": flags} if flags else {})
                s.add(i)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=firm_time.today(firm), close=price, currency=firm.base_currency,
                            source="test" if real else "seed", is_real=real))
                await s.flush()
                return i

            async def holding(firm, account, inst, *lots, value=None):
                quantity = sum(q for q, _, _ in lots)
                h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=inst.id, quantity=quantity,
                            market_value=value if value is not None else quantity * 10, cost_basis=sum(q * c for q, c, _ in lots))
                s.add(h)
                await s.flush()
                for q, c, on in lots:
                    s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=q, cost_per_unit=c, acquired_on=on))
                await s.flush()
                return h

            async def policy(mandate, firm, version, **terms):
                row = InvestmentPolicy(firm_id=firm.id, mandate_id=mandate.id, version=version, accepted_on=firm_time.today(firm), document=f"IPS v{version}",
                                       recorded_by="a@t", recorded_by_role="adviser", **terms)
                s.add(row)
                await s.flush()
                return row

            def trade(firm, account, inst, side, on, gain=None):
                s.add(Transaction(firm_id=firm.id, account_id=account.id, instrument_id=inst.id, txn_type=side, quantity=1, price=1, amount=1,
                                  trade_date=on, lineage={"realised_gain": gain} if gain is not None else {}))

            def orders(mandate, *lines):
                return {"mandate_id": str(mandate.id), "reason": "Test",
                        "orders": [{"side": side, "symbol": sym, "quantity": qty} for side, sym, qty in lines]}

            async def card(executor, inputs):
                preview = await executor.preview_change("execute_orders", inputs)
                return {r["label"]: r["value"] for r in preview["rows"]}

            async def count(model):
                return (await s.execute(select(func.count()).select_from(model))).scalar()

            nz = await firm_of("nz", "NZ", "NZD")
            elsewhere = await firm_of("o", "NZ", "NZD")
            adviser = User(id=uuid.uuid4(), firm_id=nz.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            today = firm_time.today(nz)
            ann, (m, a) = await client(nz, "Ann")
            aaa = await instrument(nz, "AAA")
            bnd = await instrument(nz, "BND", "fixed_income", 100)
            eq2 = await instrument(nz, "EQ2", price=50)
            tob = await instrument(nz, "TOB", price=5, flags=["tobacco"])
            await instrument(nz, "XXX")
            await instrument(nz, "SEED", real=False)
            nolots = await instrument(nz, "NOL")
            await holding(nz, a, aaa, (3000, 6, today - timedelta(days=500)), (5000, 12, today - timedelta(days=100)))
            s.add(Holding(firm_id=nz.id, account_id=a.id, instrument_id=nolots.id, quantity=10, market_value=0, cost_basis=0))
            await s.flush()
            ex = ToolExecutors(s, adviser.id, adviser.role, nz.id)

            print("\n=== What the account can do ===")
            await refused("selling what it does not hold", card(ex, orders(m, ("sell", "BND", 1))), "does not hold BND")
            await refused("selling more than it holds", card(ex, orders(m, ("sell", "AAA", 9000))), "holds 8000 AAA")
            await refused("a holding whose lots do not cover it", card(ex, orders(m, ("sell", "NOL", 1))), "tax lots on NOL")
            await refused("an instrument twice", card(ex, orders(m, ("buy", "BND", 1), ("buy", "BND", 2))), "BND is in this set twice")
            await refused("both sides of one instrument", card(ex, orders(m, ("sell", "AAA", 1), ("buy", "AAA", 1))), "AAA is in this set twice")
            await refused("more than the cash", card(ex, orders(m, ("buy", "BND", 300))), "short by 10,000.00")
            shown = await card(ex, orders(m, ("sell", "AAA", 2000), ("buy", "BND", 300)))
            check("a sale listed first funds a buy", "becomes" in shown["Cash, before fees"], True)
            await refused("listed after it, it does not", card(ex, orders(m, ("buy", "BND", 300), ("sell", "AAA", 2000))), "short by")
            shown = await card(ex, orders(m, ("buy", "SEED", 99999), ("buy", "BND", 1)))
            check("an order that cannot fill costs nothing", shown["Will not fill"], "SEED")
            await refused("nothing that could fill, at Confirm as well", ex.execute_orders(orders(m, ("buy", "SEED", 1))), "none of these could fill")

            print("\n=== A sale's estimated gain ===")
            shown = await card(ex, orders(m, ("sell", "AAA", 1000)))
            check("first in, first out", "gain about $4,000" in shown["Order 1"], True)
            nz.settings = {"execution": {"lot_method": str(LotRelief.HIFO)}}
            shown = await card(ex, orders(m, ("sell", "AAA", 1000)))
            check("the firm's method is the one read", "loss about $2,000" in shown["Order 1"], True)
            nz.settings = {}
            shown = await card(ex, orders(m, ("buy", "BND", 1)))
            check("a buy has none", "gain" in shown["Order 1"] or "loss" in shown["Order 1"], False)

            print("\n=== What the mandate excludes ===")
            check("no IPS on file, nothing said of one", "IPS" in await card(ex, orders(m, ("buy", "TOB", 1))), False)
            m.suitability = {**m.suitability, "values_exclusions": ["tobacco"]}
            await refused("a buy it excludes, by flag", card(ex, orders(m, ("buy", "TOB", 1))), "TOB is excluded by this mandate")
            m.suitability = {k: v for k, v in m.suitability.items() if k != "values_exclusions"}
            m.constraints = {"values_exclusions": ["TOB"]}
            await refused("and by symbol, from the constraints", card(ex, orders(m, ("buy", "TOB", 1))), "excluded by this mandate")
            m.constraints = {}

            print("\n=== The client's IPS ===")
            band = {"targets": {"equity": 0.8, "fixed_income": 0.05}, "drift_band": 0.1, "max_position": 0.85, "min_cash": 0.03}
            await policy(m, nz, 1, **band, restricted=["tobacco", "XXX"])
            shown = await card(ex, orders(m, ("buy", "BND", 10)))
            check("inside it, the card says so", shown["IPS"], "Version 1: breaches no term and widens no breach")
            await refused("a restricted flag", card(ex, orders(m, ("buy", "TOB", 1))), "TOB is restricted by IPS version 1")
            await refused("a restricted symbol", card(ex, orders(m, ("buy", "XXX", 1))), "XXX is restricted by IPS version 1")
            await refused("past the position limit", card(ex, orders(m, ("buy", "AAA", 600))), "AAA would be 86.0%", "85.0% limit")
            await refused("past the band", card(ex, orders(m, ("buy", "BND", 160))), "Fixed income would be 16.0%", "0.0% to 15.0% band")
            await refused("under the cash floor", card(ex, orders(m, ("buy", "BND", 100), ("buy", "EQ2", 150))), "Cash would be 2.5%", "3.0% floor")
            await refused("every reason is given", card(ex, orders(m, ("buy", "TOB", 1), ("buy", "XXX", 1))), "TOB is restricted", "XXX is restricted")
            await refused("one bad order stops the set", card(ex, orders(m, ("buy", "BND", 1), ("buy", "XXX", 1))), "XXX is restricted")
            await policy(m, nz, 2, targets={"equity": 0.5, "fixed_income": 0.5}, drift_band=0.1, restricted=[])
            await refused("the newest version is the one read", card(ex, orders(m, ("buy", "AAA", 10))), "IPS version 2")
            check("a sale that helps a breach goes ahead", "IPS" in await card(ex, orders(m, ("sell", "AAA", 100))), True)
            check("so does a buy that leaves it no worse", "IPS" in await card(ex, orders(m, ("buy", "BND", 1))), True)
            await refused("a breach it widens does not", card(ex, orders(m, ("buy", "AAA", 1))), "outside the 40.0% to 60.0% band")
            exception = IpsException(firm_id=nz.id, mandate_id=m.id, policy_id=(await s.execute(select(InvestmentPolicy).where(InvestmentPolicy.version == 2))).scalar_one().id,
                                     term="allocation:equity", excess=0.2, until=today + timedelta(days=30), reason="Accepted", accepted_by="a@t")
            s.add(exception)
            await s.flush()
            check("an exception covers what was accepted", "IPS" in await card(ex, orders(m, ("buy", "AAA", 50))), True)
            await refused("and no further", card(ex, orders(m, ("buy", "AAA", 200))), "outside the")
            exception.withdrawn_by = "a@t"
            await refused("a withdrawn one covers nothing", card(ex, orders(m, ("buy", "AAA", 50))), "outside the")
            await policy(m, nz, 3, **band, restricted=["AAA"])
            check("a restricted holding may be sold", "IPS" in await card(ex, orders(m, ("sell", "AAA", 100))), True)
            await refused("not added to", card(ex, orders(m, ("buy", "AAA", 1))), "AAA is restricted by IPS version 3")
            person = (await s.execute(select(Person).where(Person.household_id == ann.id))).scalar_one()
            other, _ = await mandate_of(nz, person, "Ann second")
            check("another mandate answers to its own", "IPS" in await card(ex, orders(other, ("buy", "TOB", 1))), False)
            await refused("another firm's mandate", card(ToolExecutors(s, adviser.id, adviser.role, elsewhere.id), orders(m, ("buy", "BND", 1))), "not found")

            print("\n=== Nothing is written, and Confirm asks again ===")
            before = (await count(Order), await count(TaxLot), float(a.cash_balance))
            await refused("a refusal at Confirm", ex.execute_orders(orders(m, ("buy", "AAA", 1))), "AAA is restricted")
            check("places nothing", (await count(Order), await count(TaxLot), float(a.cash_balance)), before)
            confirming = orders(m, ("buy", "BND", 1))
            await card(ex, confirming)
            await policy(m, nz, 4, **band, restricted=["BND"])
            await refused("the IPS changed after the card", ex.execute_orders(confirming), "BND is restricted by IPS version 4")
            check("and nothing was placed", await count(Order), 0)
            await policy(m, nz, 5, targets={"equity": 0.8, "fixed_income": 0.05}, drift_band=0.25, restricted=[])
            done = await ex.execute_orders(orders(m, ("sell", "AAA", 100), ("buy", "BND", 1)))
            check("a set that passes settles", done["orders_settled"], 2)
            sold = (await s.execute(select(Order).where(Order.side == "sell"))).scalar_one()
            check("the sale carries its estimate", float(sold.est_realised_gain), 400.0)
            check("and settlement agrees, but for fees", abs(float(sold.realised_gain) - 400.0) < 25, True)

            print("\n=== A household taxed in the US ===")
            us = await firm_of("us", "US", "USD")
            usday = firm_time.today(us)
            usx = ToolExecutors(s, adviser.id, adviser.role, us.id)
            lee, (um, ua) = await client(us, "Lee")
            person = (await s.execute(select(Person).where(Person.household_id == lee.id))).scalar_one()
            _, spouse = await mandate_of(us, person, "Lee IRA")
            lll, ggg, old, won = [await instrument(us, n) for n in ("LLL", "GGG", "OLD", "WON")]
            await holding(us, ua, lll, (100, 15, usday - timedelta(days=400)))
            await holding(us, ua, ggg, (100, 5, usday - timedelta(days=10)))
            trade(us, spouse, lll, "sell", usday - timedelta(days=5), -500)
            await s.flush()
            await refused("a rebuy after a loss in the household's other account", card(usx, orders(um, ("buy", "LLL", 1))),
                          "Buying LLL would be a wash sale", "Lee IRA A/C", (usday + timedelta(days=26)).isoformat())
            await refused("at Confirm as well", usx.execute_orders(orders(um, ("buy", "LLL", 1))), "wash sale")
            trade(us, spouse, lll, "buy", usday - timedelta(days=3))
            await s.flush()
            await refused("a sale at a loss after a purchase", card(usx, orders(um, ("sell", "LLL", 1))), "Selling LLL at a loss would be a wash sale")
            check("a sale at a gain is no wash sale", "gain about" in (await card(usx, orders(um, ("sell", "GGG", 1))))["Order 1"], True)
            trade(us, spouse, old, "sell", usday - timedelta(days=31), -500)
            trade(us, spouse, won, "sell", usday - timedelta(days=5), 500)
            await s.flush()
            check("a window that has closed", "Order 1" in await card(usx, orders(um, ("buy", "OLD", 1))), True)
            check("a sale at a gain binds nothing", "Order 1" in await card(usx, orders(um, ("buy", "WON", 1))), True)
            lee.values = {"jurisdiction": "NZ"}
            await s.flush()
            check("a household taxed elsewhere is not bound", "Order 1" in await card(usx, orders(um, ("buy", "LLL", 1))), True)
            lee.values = {}
            trade(nz, a, bnd, "sell", today - timedelta(days=5), -500)
            await s.flush()
            await s.delete((await s.execute(select(InvestmentPolicy).where(InvestmentPolicy.version == 4))).scalar_one())
            await s.flush()
            check("nor is a New Zealand firm's", "Order 1" in await card(ex, orders(m, ("buy", "BND", 1))), True)

            print("\n=== What Astra may say about it ===")
            check("a mandate's type is not a rule", "A mandate's type is a label" in SYSTEM_PROMPT_TEMPLATE, True)
            check("the licence rule says who it governs", "A firm with no licences on file is not governed" in TOOLS["read_licences"].description, True)
            check("the tool is called, not second-guessed", "call it rather than working out the answer first" in TOOLS["execute_orders"].description, True)

            print("\n=== An Indian discretionary mandate ===")
            india = await firm_of("in", "IN", "INR", regulator="SEBI", settings={"licences": ["pms_discretionary"]})
            inday = firm_time.today(india)
            inx = ToolExecutors(s, adviser.id, adviser.role, india.id)
            _, (im, ia) = await client(india, "Rao", cash=1000000, constraints={"service_track": "pms_discretionary"})
            infy, wip, tcs, hdfc, clean = [await instrument(india, n) for n in ("INFY", "WIP", "TCS", "HDFC", "ITC")]
            await holding(india, ia, tcs, (100, 5, inday - timedelta(days=400)))
            await holding(india, ia, hdfc, (100, 20, inday - timedelta(days=60)))
            trade(india, ia, infy, "sell", inday, -100)
            trade(india, ia, tcs, "buy", inday)
            trade(india, ia, wip, "sell", inday - timedelta(days=5), -100)
            s.add(CorporateAction(firm_id=india.id, instrument_id=hdfc.id, action_type="bonus_issue", status="announced", record_date=inday - timedelta(days=30), details={}))
            await s.flush()
            await refused("a same-day rebuy", card(inx, orders(im, ("buy", "INFY", 1))), "net against that sale")
            await refused("a same-day sale", card(inx, orders(im, ("sell", "TCS", 1))), "net against that purchase")
            await refused("a loss inside a bonus-stripping window", card(inx, orders(im, ("sell", "HDFC", 1))), "s.175(9)")
            shown = await card(inx, orders(im, ("buy", "WIP", 1)))
            check("a rebuy after a loss is a flag, not a refusal", "Sold at a loss in Rao A/C" in shown["Review WIP"], True)
            shown = await card(inx, orders(im, ("buy", "ITC", 1)))
            check("nothing to flag, nothing said", [k for k in shown if k.startswith("Review")], [])
            check("and the US window does not bind it", "Order 1" in shown, True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
