"""End-to-end proof that an approved order set actually moves the book.

Runs against a throwaway SQLite database so it needs no cloud connection. What it proves
is the part that was missing: that placing and settling an order changes cash, position,
tax lots and the transaction ledger by the right amounts, and that the guards refuse the
things that should be refused.

    python -m tests.verify_orders
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import orders as order_engine
from app.conduit import execution
from app.core.db import Base
from app.models.enums import LotRelief, OrderStatus
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Execution, Order

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float, Decimal)) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Only the tables this exercises. Creating the whole schema pulls in the pgvector
    # columns, which SQLite cannot build — and the trade path does not touch them.
    wanted = ["firm", "account", "instrument", "price", "holding", "tax_lot", "txn",
              "order", "execution"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test",
                    settings={"execution": {"fee_bps": 10, "fee_minimum": 5,
                                            "lot_method": LotRelief.FIFO}})
        acct = Account(id=uuid.uuid4(), firm_id=firm.id, name="A1", currency="NZD",
                       cash_balance=Decimal("50000"))
        inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha",
                          asset_class="equity")
        s.add_all([firm, acct, inst])
        await s.flush()

        # A real price, and an older synthetic one that must never be chosen to fill on.
        s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=date.today(),
                    close=Decimal("100"), currency="NZD", source="yahoo", is_real=True))
        # An existing position: two lots at different costs, so FIFO vs HIFO differ.
        holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id,
                          instrument_id=inst.id, quantity=Decimal("300"),
                          market_value=Decimal("30000"), cost_basis=Decimal("22000"))
        s.add(holding)
        await s.flush()
        s.add_all([
            TaxLot(firm_id=firm.id, holding_id=holding.id, quantity=Decimal("100"),
                   cost_per_unit=Decimal("60"), acquired_on=date.today() - timedelta(days=400)),
            TaxLot(firm_id=firm.id, holding_id=holding.id, quantity=Decimal("200"),
                   cost_per_unit=Decimal("80"), acquired_on=date.today() - timedelta(days=100)),
        ])
        await s.commit()

        print("\n=== 1. State machine refuses illegal moves ===")
        o = Order(firm_id=firm.id, account_id=acct.id, instrument_id=inst.id,
                  side="buy", quantity=Decimal("1"), history=[])
        try:
            order_engine.transition(o, OrderStatus.FILLED, actor="test")
            check("draft -> filled is refused", "allowed", "refused")
        except order_engine.OrderTransitionError:
            check("draft -> filled is refused", "refused", "refused")

        order_engine.transition(o, OrderStatus.STAGED, actor="test")
        order_engine.transition(o, OrderStatus.CANCELLED, actor="test")
        try:
            order_engine.transition(o, OrderStatus.PLACED, actor="test")
            check("cancelled is terminal", "allowed", "refused")
        except order_engine.OrderTransitionError:
            check("cancelled is terminal", "refused", "refused")
        check("history records every move", len(o.history), 2)

        print("\n=== 2. SELL 150 @ 100, FIFO relief ===")
        # FIFO: 100 units @60 (=6000) + 50 @80 (=4000) => cost relieved 10000.
        # Gross 15000, fee 10bps = 15.00 => realised = 15000 - 15 - 10000 = 4985.
        result = await execution.execute_order_set(
            s, firm=firm, order_set=[{
                "side": "sell", "symbol": "AAA", "instrument_id": str(inst.id),
                "account_id": str(acct.id), "quantity": 150, "est_price": 100,
                "est_value": 15000, "reason": "trim",
            }])
        await s.commit()

        check("order settled", result["orders_settled"], 1)
        check("realised gain", result["realised_gain"], 4985.00)
        check("fees", result["fees"], 15.00)
        check("venue is honest about market access", result["reaches_market"], False)

        await s.refresh(holding)
        await s.refresh(acct)
        check("holding reduced 300 -> 150", holding.quantity, 150)
        check("cost basis relieved 22000 -> 12000", holding.cost_basis, 12000)
        check("cash 50000 + 15000 - 15", acct.cash_balance, 64985.00)

        lots = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
        check("oldest lot fully consumed, one lot left", len(lots), 1)
        check("remaining lot is the 80-cost one", float(lots[0].cost_per_unit), 80)
        check("remaining lot quantity", lots[0].quantity, 150)

        txns = (await s.execute(select(Transaction))).scalars().all()
        check("transaction written (the table analytics reads)", len(txns), 1)
        check("txn carries realised gain in lineage",
              txns[0].lineage.get("realised_gain"), 4985.00)

        print("\n=== 3. Settlement is idempotent ===")
        ex = (await s.execute(select(Execution))).scalars().first()
        again = await order_engine.settle(s, ex)
        await s.commit()
        check("re-settling the same fill is a no-op", again["settled"], False)
        await s.refresh(acct)
        check("cash unchanged by the retry", acct.cash_balance, 64985.00)

        print("\n=== 4. Guards refuse what they should ===")
        oversold = await execution.execute_order_set(
            s, firm=firm, order_set=[{
                "side": "sell", "symbol": "AAA", "instrument_id": str(inst.id),
                "account_id": str(acct.id), "quantity": 9999, "est_price": 100,
            }])
        await s.commit()
        # The venue filled it; the book refused it. That is a settlement break, not a
        # rejected order — the fill really happened and must stay visible.
        check("overselling produces a settlement break", oversold["settlement_breaks"], 1)
        check("not miscounted as a failed placement", oversold["orders_failed"], 0)
        broken = (await s.execute(
            select(Order).where(Order.settlement_status == "failed"))).scalars().all()
        check("the break is recorded on the order", len(broken), 1)
        check("break says why", "exceeds" in (broken[0].rejected_reason or ""), True)
        await s.refresh(acct)
        check("cash untouched by the broken settlement", acct.cash_balance, 64985.00)
        await s.refresh(holding)
        check("position untouched by the broken settlement", holding.quantity, 150)

        # An instrument whose only price is synthetic must not be filled on.
        inst2 = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BBB", name="Beta",
                           asset_class="equity")
        s.add(inst2)
        await s.flush()
        s.add(Price(firm_id=firm.id, instrument_id=inst2.id, as_of=date.today(),
                    close=Decimal("50"), currency="NZD", source="synthetic", is_real=False))
        await s.commit()
        synth = await execution.execute_order_set(
            s, firm=firm, order_set=[{
                "side": "buy", "symbol": "BBB", "instrument_id": str(inst2.id),
                "account_id": str(acct.id), "quantity": 10, "est_price": 50,
            }])
        await s.commit()
        check("refuses to fill on a synthetic price", synth["orders_failed"], 1)
        check("and says why", "synthetic" in synth["failed"][0]["reason"], True)
        check("a refused placement is not a break", synth["settlement_breaks"], 0)

        print("\n=== 5. BUY 100 @ 100 opens a lot and spends cash ===")
        buy = await execution.execute_order_set(
            s, firm=firm, order_set=[{
                "side": "buy", "symbol": "AAA", "instrument_id": str(inst.id),
                "account_id": str(acct.id), "quantity": 100, "est_price": 100,
            }])
        await s.commit()
        check("buy settled", buy["orders_settled"], 1)
        await s.refresh(holding)
        await s.refresh(acct)
        check("holding 150 -> 250", holding.quantity, 250)
        check("cash 64985 - 10000 - 10", acct.cash_balance, 54975.00)
        lots = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
        check("a new tax lot opened", len(lots), 2)
        new_lot = [l for l in lots if float(l.cost_per_unit) != 80][0]
        check("fees capitalised into the new lot's cost", float(new_lot.cost_per_unit), 100.10)

        print("\n=== 6. A filled order cannot be reversed, only offset ===")
        filled = (await s.execute(
            select(Order).where(Order.status == OrderStatus.FILLED).limit(1))).scalar_one()
        out = await order_engine.cancel(s, filled, actor="adviser")
        check("cancel of a filled order refused", out["cancelled"], False)
        check("and explains offsetting", "offset" in out["reason"], True)

    await engine.dispose()  # otherwise the process hangs on the open pool at exit
    print(f"\n{'=' * 62}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
