"""Prove the sleeve netting arithmetic (L200-3 §6.2) does what the module's own worked
example does — crosses conflicting sleeve intents into one net order and attributes the
crossed vs. external portion back to each sleeve fairly — and that reconciliation (§6.1)
catches a flat holding whose sleeve attributions do not sum to it, plus an attribution
pointing at a holding that is not actually in that account.

Runs against a throwaway SQLite database (for the reconciliation half) and pure Python (for
the netting half, which is deliberately DB-free — the same discipline as rebalancing.py).

    python -m tests.verify_sleeves
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import sleeves as engine
from app.aurea_core.sleeves import SleeveIntent
from app.core.db import Base
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument, ModelPortfolio
from app.models.sleeves import Sleeve, SleeveHolding
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.001) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def test_netting() -> None:
    print("\n=== netting: 600 shares of gross intent become one 300-share external order ===")
    intents = [
        SleeveIntent(sleeve_id="s1", account_id="a1", instrument_id="i1", symbol="AAA", side="buy", quantity=100),
        SleeveIntent(sleeve_id="s3", account_id="a1", instrument_id="i1", symbol="AAA", side="buy", quantity=100),
        SleeveIntent(sleeve_id="s2", account_id="a1", instrument_id="i1", symbol="AAA", side="sell", quantity=500),
    ]
    [order] = engine.net_intents(intents)
    check("gross buy quantity", order.gross_buy_quantity, 200)
    check("gross sell quantity", order.gross_sell_quantity, 500)
    check("crossed quantity = the smaller side", order.crossed_quantity, 200)
    check("net side is sell (the larger side)", order.side, "sell")
    check("net external quantity", order.quantity, 300)

    by_sleeve = {a["sleeve_id"]: a for a in order.sleeve_allocations}
    check("s1's buy intent is fully crossed internally", by_sleeve["s1"]["crossed_quantity"], 100)
    check("s1 needs nothing external", by_sleeve["s1"]["external_quantity"], 0)
    check("s3's buy intent is fully crossed internally", by_sleeve["s3"]["crossed_quantity"], 100)
    check("s2 is crossed for 200 of its 500-share sell", by_sleeve["s2"]["crossed_quantity"], 200)
    check("s2 carries the entire 300-share external order", by_sleeve["s2"]["external_quantity"], 300)

    print("\n=== netting: exactly balanced intents cross entirely, nothing trades ===")
    balanced = [
        SleeveIntent(sleeve_id="s1", account_id="a1", instrument_id="i2", symbol="BBB", side="buy", quantity=100),
        SleeveIntent(sleeve_id="s2", account_id="a1", instrument_id="i2", symbol="BBB", side="sell", quantity=100),
    ]
    [order2] = engine.net_intents(balanced)
    check("fully crossed order has no side", order2.side, None)
    check("fully crossed order trades zero shares", order2.quantity, 0)
    check("fully crossed quantity equals the whole intent", order2.crossed_quantity, 100)

    print("\n=== netting: an uncontested intent just passes through ===")
    solo = [SleeveIntent(sleeve_id="s1", account_id="a1", instrument_id="i3", symbol="CCC", side="buy", quantity=50)]
    [order3] = engine.net_intents(solo)
    check("solo intent side", order3.side, "buy")
    check("solo intent quantity", order3.quantity, 50)
    check("solo intent has nothing crossed", order3.crossed_quantity, 0)

    print("\n=== netting: different instruments and accounts never net against each other ===")
    mixed = [
        SleeveIntent(sleeve_id="s1", account_id="a1", instrument_id="i1", symbol="AAA", side="buy", quantity=10),
        SleeveIntent(sleeve_id="s1", account_id="a1", instrument_id="i4", symbol="DDD", side="sell", quantity=10),
        SleeveIntent(sleeve_id="s1", account_id="a2", instrument_id="i1", symbol="AAA", side="sell", quantity=10),
    ]
    results = engine.net_intents(mixed)
    check("three distinct (account, instrument) groups stay separate", len(results), 3)
    for o in results:
        check(f"group {o.account_id}/{o.instrument_id} is untouched by the others", o.quantity, 10)


async def test_reconcile() -> int:
    print("\n=== reconciliation: a shortfall, a clean holding, and an orphaned attribution ===")
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "account", "instrument", "holding", "model_portfolio", "sleeve", "sleeve_holding"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        acct = Account(id=uuid.uuid4(), firm_id=firm.id, name="A1", currency="USD",
                        cash_balance=Decimal("0"))
        inst_a = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha", asset_class="equity")
        inst_b = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BBB", name="Beta", asset_class="fixed_income")
        model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="M")
        s.add_all([firm, acct, inst_a, inst_b, model])
        await s.flush()

        # Fully attributed holding: 60 + 40 = 100, clean.
        clean_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id,
                                 instrument_id=inst_a.id, quantity=Decimal("100"),
                                 market_value=Decimal("10000"), cost_basis=Decimal("6000"))
        # Short-attributed holding: only 60 of 100 attributed -> a 40-share break.
        short_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id,
                                 instrument_id=inst_b.id, quantity=Decimal("100"),
                                 market_value=Decimal("5000"), cost_basis=Decimal("4000"))
        s.add_all([clean_holding, short_holding])
        await s.flush()

        sleeve1 = Sleeve(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id, model_id=model.id,
                          name="Core", target_weight=0.6, status="active")
        sleeve2 = Sleeve(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id, model_id=model.id,
                          name="Satellite", target_weight=0.4, status="active")
        s.add_all([sleeve1, sleeve2])
        await s.flush()

        s.add_all([
            SleeveHolding(firm_id=firm.id, sleeve_id=sleeve1.id, holding_id=clean_holding.id,
                          quantity=Decimal("60"), cost_basis=Decimal("3600")),
            SleeveHolding(firm_id=firm.id, sleeve_id=sleeve2.id, holding_id=clean_holding.id,
                          quantity=Decimal("40"), cost_basis=Decimal("2400")),
            SleeveHolding(firm_id=firm.id, sleeve_id=sleeve1.id, holding_id=short_holding.id,
                          quantity=Decimal("60"), cost_basis=Decimal("2400")),
            # Orphaned: this sleeve (in this account) attributes shares to a holding that
            # belongs to no account at all — a fabricated, unreachable holding id.
            SleeveHolding(firm_id=firm.id, sleeve_id=sleeve2.id, holding_id=uuid.uuid4(),
                          quantity=Decimal("15"), cost_basis=Decimal("900")),
        ])
        await s.flush()

        result = await engine.reconcile(s, acct.id)
        check("two holdings checked", result["holdings_checked"], 2)
        check("exactly one break found", len(result["breaks"]), 1)
        broken = result["breaks"][0]
        check("the break is the short-attributed holding", broken["holding_id"], str(short_holding.id))
        check("unattributed quantity on the break", broken["unattributed_quantity"], 40)
        check("one orphaned attribution found", len(result["orphaned_attributions"]), 1)
        check("reconciliation is not clean", result["clean"], False)

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    return 1 if FAIL else 0


def main() -> int:
    test_netting()
    rc = asyncio.run(test_reconcile())

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else rc


if __name__ == "__main__":
    sys.exit(main())
