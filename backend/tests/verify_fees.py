"""Prove the client-facing fee panel (GET /api/canvas/fees) bills against the fee schedule
confirmed at onboarding (Mandate.fee_schedule_id) when one exists, and only falls back to
the segment-based estimate when it doesn't — and says which it used.

Before this, onboarding confirmed a FeeSchedule under maker/checker that nothing ever read
again, so a client could be shown a rate the firm never agreed with them.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_fees
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.api.canvas import get_fees
from app.core.db import Base
from app.core.security import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm, FirmSegment

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "household", "person", "mandate", "account", "instrument", "holding",
              "fee_schedule", "firm_segment"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test", base_currency="NZD")
        s.add(firm)
        await s.flush()
        # The segment estimate says 100 bps — deliberately different from every schedule below,
        # so a result can only come from the right source.
        s.add(FirmSegment(firm_id=firm.id, slug="core", label="Core", fee_tier_bps=100,
                          min_aum_usd=0, is_active=True))
        tiered = FeeSchedule(
            id=uuid.uuid4(), firm_id=firm.id, code="TIER", name="Tiered", fee_type="tiered_bps",
            tiers=[{"min_aum": 0, "max_aum": 1_000_000, "bps": 100},
                   {"min_aum": 1_000_000, "max_aum": None, "bps": 50}],
            currency="NZD")
        minimum = FeeSchedule(
            id=uuid.uuid4(), firm_id=firm.id, code="MIN", name="Flat with minimum",
            fee_type="flat_bps", flat_bps=Decimal("40"), minimum_annual_fee=Decimal("5000"),
            tiers=[], currency="NZD")
        s.add_all([tiered, minimum])
        inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha",
                          asset_class="equity")
        s.add(inst)
        await s.flush()

        async def household(name: str, aum: float, fee_schedule_id=None) -> uuid.UUID:
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
            p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name=f"{name} owner")
            s.add_all([hh, p])
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate",
                        mandate_type="advisory", fee_schedule_id=fee_schedule_id)
            s.add(m)
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} account",
                        currency="NZD", cash_balance=Decimal("0"))
            s.add(a)
            await s.flush()
            s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=inst.id,
                          quantity=Decimal("1"), market_value=Decimal(str(aum)),
                          cost_basis=Decimal(str(aum))))
            await s.flush()
            return hh.id

        user = SimpleNamespace(role=UserRole.ADVISER, person_id=None)

        async def fees(hid):
            return await get_fees(household_id=hid, user=user, firm=firm, db=s)

        print("\n=== No confirmed schedule: segment estimate, labelled as an estimate ===")
        r = await fees(await household("NoSchedule", 2_000_000))
        check("source is the segment estimate", r["source"], "segment_estimate")
        check("priced at the segment's 100 bps", r["fee_bps"], 100)
        check("annual fee is 100 bps of 2,000,000", r["annual_fee"], 20_000)

        print("\n=== Confirmed tiered schedule: billed marginally, not top band on everything ===")
        r = await fees(await household("Tiered", 2_000_000, tiered.id))
        check("source is the confirmed schedule", r["source"], "confirmed_fee_schedule")
        # 1,000,000 @ 100 bps = 10,000 plus 1,000,000 @ 50 bps = 5,000
        check("annual fee is marginal: 10,000 + 5,000", r["annual_fee"], 15_000)
        check("effective rate is 75 bps, not the 50 top band or the 100 segment", r["fee_bps"], 75)
        check("monthly fee is a twelfth of annual", r["monthly_fee"], 1_250)
        check("the schedule is named", r["fee_schedule_name"], "Tiered")
        check("the breakdown shows both bands", len(r["breakdown"]), 2)

        print("\n=== Confirmed schedule overrides a segment that would price differently ===")
        r = await fees(await household("TieredSmall", 500_000, tiered.id))
        check("500,000 sits entirely in the 100 bps band", r["annual_fee"], 5_000)
        r_seg = await fees(await household("SegmentSmall", 500_000))
        check("the same AUM with no schedule uses the segment estimate", r_seg["source"], "segment_estimate")

        print("\n=== Minimum annual fee is applied ===")
        # 40 bps of 500,000 = 2,000, below the 5,000 minimum
        r = await fees(await household("Minimum", 500_000, minimum.id))
        check("the minimum raises the fee to 5,000", r["annual_fee"], 5_000)
        check("effective rate reflects the minimum (100 bps)", r["fee_bps"], 100)

    await db.dispose()  # otherwise the process hangs on the open pool at exit

    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
