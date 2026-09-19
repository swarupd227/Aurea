"""Prove the corporate-actions arithmetic (L200-4 §5.1) does what each event type is
supposed to do: a cash dividend adds cash and books a Transaction, a split rescales
quantity and cost-per-unit so total cost basis is unchanged, a return of capital reduces
basis and cash without touching quantity, and every entitlement posts exactly once.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_corporate_actions
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import corporate_actions as engine
from app.aurea_core.corporate_actions import CorporateActionError
from app.core.db import Base
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument, TaxLot, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float, Decimal)) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "account", "instrument", "holding", "tax_lot", "txn",
              "corporate_action", "corporate_action_entitlement"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        acct = Account(id=uuid.uuid4(), firm_id=firm.id, name="A1", currency="USD",
                        cash_balance=Decimal("1000"))
        inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha",
                           asset_class="equity")
        s.add_all([firm, acct, inst])
        await s.flush()

        holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id,
                           instrument_id=inst.id, quantity=Decimal("100"),
                           market_value=Decimal("10000"), cost_basis=Decimal("6000"))
        s.add(holding)
        await s.flush()
        s.add(TaxLot(firm_id=firm.id, holding_id=holding.id, quantity=Decimal("100"),
                      cost_per_unit=Decimal("60"), acquired_on=date(2024, 1, 15)))
        await s.flush()

        print("\n=== entitlement computation: one row per account holding the instrument ===")
        div = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=inst.id,
                               action_type="cash_dividend", status="announced", is_voluntary=False,
                               payable_date=date(2026, 3, 1), details={"cash_per_share": 2.0})
        s.add(div)
        await s.flush()
        created = await engine.compute_entitlements(s, div.id)
        check("one entitlement created for the one holding account", len(created), 1)
        again = await engine.compute_entitlements(s, div.id)
        check("computing entitlements twice creates nothing the second time", len(again), 0)

        print("\n=== cash dividend: cash posted, Transaction booked, shares unchanged ===")
        ent = created[0]
        posted = await engine.post_entitlement(s, ent.id)
        check("cash amount = 100 shares x $2.00", posted.cash_amount, 200.0)
        await s.refresh(acct)
        check("account cash balance increased by the dividend", acct.cash_balance, 1200.0)
        await s.refresh(holding)
        check("dividend does not change share quantity", holding.quantity, 100.0)
        txns = (await s.execute(select(Transaction).where(Transaction.account_id == acct.id))).scalars().all()
        check("a dividend Transaction was booked", len(txns), 1)
        check("Transaction amount matches the posted cash", txns[0].amount, 200.0)

        print("\n=== a corporate action never posts twice ===")
        try:
            await engine.post_entitlement(s, ent.id)
            check("double-post raises CorporateActionError", "no exception", "CorporateActionError")
        except CorporateActionError:
            check("double-post raises CorporateActionError", True, True)

        print("\n=== 2-for-1 split: quantity doubles, cost-per-unit halves, total basis unchanged ===")
        split = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=inst.id,
                                 action_type="split", status="announced", is_voluntary=False,
                                 details={"ratio": 2.0})
        s.add(split)
        await s.flush()
        [split_ent] = await engine.compute_entitlements(s, split.id)
        await engine.post_entitlement(s, split_ent.id)
        await s.refresh(holding)
        check("quantity doubled", holding.quantity, 200.0)
        lots = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
        check("one tax lot", len(lots), 1)
        check("lot quantity doubled", lots[0].quantity, 200.0)
        check("lot cost-per-unit halved", lots[0].cost_per_unit, 30.0)
        total_basis = sum(float(l.quantity) * float(l.cost_per_unit) for l in lots)
        check("total cost basis unchanged by a split", total_basis, 6000.0)

        print("\n=== return of capital: basis and cash fall, quantity untouched ===")
        roc = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=inst.id,
                               action_type="return_of_capital", status="announced", is_voluntary=False,
                               payable_date=date(2026, 6, 1), details={"cash_per_share": 5.0})
        s.add(roc)
        await s.flush()
        [roc_ent] = await engine.compute_entitlements(s, roc.id)
        check("RoC entitlement sees the post-split share count", roc_ent.shares_entitled, 200.0)
        await engine.post_entitlement(s, roc_ent.id)
        await s.refresh(acct)
        # 200 shares x $5.00 = $1000 returned; prior cash was 1200.
        check("RoC cash amount", roc_ent.cash_amount, 1000.0)
        check("account cash balance increased by the RoC amount", acct.cash_balance, 2200.0)
        await s.refresh(holding)
        lots = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
        # $1000 of basis returned across 200 shares -> cost_per_unit falls by $5: 30 -> 25.
        check("lot cost-per-unit reduced by the per-share return", lots[0].cost_per_unit, 25.0)
        check("holding.quantity untouched by RoC", holding.quantity, 200.0)

        print("\n=== a voluntary event records the human's cost-basis call, not an invented one ===")
        merger = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=inst.id,
                                  action_type="merger", status="announced", is_voluntary=True,
                                  details={"options": ["cash", "stock"]})
        s.add(merger)
        await s.flush()
        [merger_ent] = await engine.compute_entitlements(s, merger.id)
        await s.refresh(merger)
        check("a voluntary event opens for election", merger.status, "election_open")
        elected = await engine.elect(s, merger_ent.id, choice="stock", actor="adviser@test")
        check("election recorded", elected.election_choice, "stock")
        check("election status", elected.status, "elected")
        try:
            await engine.elect(s, merger_ent.id, choice="cash", actor="adviser@test")
            check("re-electing after election is refused", "no exception", "CorporateActionError")
        except CorporateActionError:
            check("re-electing after election is refused", True, True)
        posted_merger = await engine.post_entitlement(
            s, merger_ent.id, cash_amount=0, share_amount=42,
            cost_basis_adjustment={"kind": "merger_fair_value", "allocated_by": "adviser@test"},
        )
        check("merger posting keeps the human-supplied basis note, not a computed one",
              posted_merger.cost_basis_adjustment["kind"], "merger_fair_value")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
