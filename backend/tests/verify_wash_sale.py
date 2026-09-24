"""Prove the household wash-sale calendar (L200-4 §7.2) does what the account-scoped
check it replaces could not: a loss lot in one account is flagged when the SAME
instrument was bought recently in a DIFFERENT account in the same household (the spouse's
account / an IRA case) — and, the precise fix this pass made — a GAIN lot is never flagged
no matter how recently the instrument was rebought, because IRC §1091 only disallows
losses.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_wash_sale
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import tax_intelligence
from app.core.db import Base
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, TaxLot
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    # household_brain() unconditionally touches legal_entity/goal/relationship_edge and
    # account_valuation() touches price, even though this scenario has none of those rows.
    wanted = ["firm", "household", "person", "legal_entity", "mandate", "account",
              "instrument", "price", "holding", "tax_lot", "goal", "relationship_edge"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        household = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Test Family")
        # Two spouses, each with their own mandate/account — the case a single-account
        # check cannot see across.
        wife = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=household.id, full_name="Wife")
        husband = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=household.id, full_name="Husband")
        s.add_all([firm, household, wife, husband])
        await s.flush()

        mandate_a = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=wife.id, name="Wife IRA",
                            mandate_type="advisory")
        mandate_b = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=husband.id, name="Husband Taxable",
                            mandate_type="advisory")
        s.add_all([mandate_a, mandate_b])
        await s.flush()

        account_a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_a.id,
                             name="Wife IRA A/C", currency="USD", cash_balance=Decimal("0"))
        account_b = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_b.id,
                             name="Husband Taxable A/C", currency="USD", cash_balance=Decimal("0"))
        s.add_all([account_a, account_b])
        await s.flush()

        loss_inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="LOSS", name="Loss Co",
                                asset_class="equity")
        gain_inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="GAIN", name="Gain Co",
                                asset_class="equity")
        s.add_all([loss_inst, gain_inst])
        await s.flush()

        today = date.today()

        # LOSS scenario: husband holds an old lot of LOSS trading below cost; wife's IRA
        # bought LOSS 10 days ago. Cross-account, cross-mandate — must still be flagged.
        loss_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account_b.id,
                                instrument_id=loss_inst.id, quantity=Decimal("100"),
                                market_value=Decimal("6000"), cost_basis=Decimal("10000"))
        wife_loss_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account_a.id,
                                     instrument_id=loss_inst.id, quantity=Decimal("20"),
                                     market_value=Decimal("1200"), cost_basis=Decimal("1200"))
        s.add_all([loss_holding, wife_loss_holding])
        await s.flush()

        s.add_all([
            TaxLot(firm_id=firm.id, holding_id=loss_holding.id, quantity=Decimal("100"),
                   cost_per_unit=Decimal("100"), acquired_on=today - timedelta(days=200)),
            TaxLot(firm_id=firm.id, holding_id=wife_loss_holding.id, quantity=Decimal("20"),
                   cost_per_unit=Decimal("60"), acquired_on=today - timedelta(days=10)),
        ])

        # GAIN scenario: husband holds an old lot of GAIN trading above cost; wife's IRA
        # also bought GAIN 10 days ago. Same recent-purchase shape as the loss case — but
        # this lot must NEVER be flagged, because it would realise a gain, not a loss.
        gain_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account_b.id,
                                instrument_id=gain_inst.id, quantity=Decimal("50"),
                                market_value=Decimal("10000"), cost_basis=Decimal("5000"))
        wife_gain_holding = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account_a.id,
                                     instrument_id=gain_inst.id, quantity=Decimal("10"),
                                     market_value=Decimal("2000"), cost_basis=Decimal("2000"))
        s.add_all([gain_holding, wife_gain_holding])
        await s.flush()

        s.add_all([
            TaxLot(firm_id=firm.id, holding_id=gain_holding.id, quantity=Decimal("50"),
                   cost_per_unit=Decimal("100"), acquired_on=today - timedelta(days=200)),
            TaxLot(firm_id=firm.id, holding_id=wife_gain_holding.id, quantity=Decimal("10"),
                   cost_per_unit=Decimal("200"), acquired_on=today - timedelta(days=10)),
        ])
        await s.flush()

        result = await tax_intelligence.household_wash_sale_calendar(s, household.id, firm_id=firm.id)

        print("\n=== cross-account loss lot is flagged (spouse's IRA, not the same account) ===")
        by_symbol = {v["symbol"]: v for v in result["violations"]}
        check("exactly one violation (the loss lot only)", len(result["violations"]), 1)
        check("the flagged lot is LOSS, not GAIN", "LOSS" in by_symbol, True)
        check("GAIN lot is not flagged despite an equally recent repurchase", "GAIN" in by_symbol, False)

        loss_violation = by_symbol["LOSS"]
        # 100 shares * (100 cost - 60 current) = $4,000 disallowed.
        check("disallowed loss computed correctly", loss_violation["wash_sale_disallowed_loss"], 4000.0)
        check("total_disallowed_loss matches the one violation", result["total_disallowed_loss"], 4000.0)
        check("the conflicting purchase is attributed to the wife's account, not husband's",
              loss_violation["wash_sale_conflicts"][0]["account_name"], "Wife IRA A/C")
        check("account_name on the flagged lot itself is the husband's (where the loss sits)",
              loss_violation["account_name"], "Husband Taxable A/C")

        print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
        for f in FAIL:
            print(f"  FAILED: {f}")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
