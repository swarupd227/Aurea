"""Prove reconciliation to agreement (L200 §5): a bill is re-derived from the fee agreement
independently of how it was drafted, and the findings L200 names fire - a fee that does not
match its schedule, a schedule changed after sign-off, broken dual control, a duplicate
period, a first bill unrelated to the balance the fee was agreed on - and, as important, do
NOT fire on an honest bill.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_billing_reconcile
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
from app.aurea_core.billing_reconcile import merge_checks, reconcile_invoice
from app.core.db import Base
from app.models.billing import BillingRun, FeeArrangement, Invoice
from app.models.graph import Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.tenant import Firm

PASS, FAIL = [], []
LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)
Q3 = (date(2026, 7, 1), date(2026, 9, 30))


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def blocks(checks):
    return {c["code"] for c in checks if c["severity"] == "block"}


def warns(checks):
    return {c["code"] for c in checks if c["severity"] == "warn"}


async def run() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "person", "mandate", "fee_schedule", "fee_arrangement", "billing_run", "invoice"]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[Base.metadata.tables[n] for n in wanted]))
    Session = async_sessionmaker(db, expire_on_commit=False)

    try:
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
            s.add(firm)
            await s.flush()
            tiered = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="T", name="Tiered", fee_type="tiered_bps",
                                 tiers=[{"min_aum": 0, "max_aum": 1_000_000, "bps": 100},
                                        {"min_aum": 1_000_000, "max_aum": None, "bps": 75}],
                                 minimum_annual_fee=Decimal("3000"), currency="NZD")
            wild = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="W", name="Typo", fee_type="flat_bps",
                               flat_bps=Decimal("500"), tiers=[], currency="NZD")
            s.add_all([tiered, wild])
            run_row = BillingRun(firm_id=firm.id, as_of=date(2026, 10, 5), status="proposed", summary={})
            s.add(run_row)
            await s.flush()

            async def arrangement(*, schedule=tiered, set_by="maker@x", confirmed_by="checker@x", confirmed_at=LONG_AGO,
                                  status="active", agreed=None):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, full_name="P")
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name="M", mandate_type="advisory")
                s.add(m)
                await s.flush()
                arr = await billing.activate_arrangement(
                    s, firm_id=firm.id, mandate=m, fee_schedule_id=schedule.id, billing_method="arrears",
                    billing_frequency="quarterly", householding=False, effective_from=date(2020, 1, 1),
                    set_by=set_by, set_at=LONG_AGO, confirmed_by=confirmed_by,
                    confirmed_at=confirmed_at if confirmed_by else None, source="seed", agreed_billable_aum=agreed)
                arr.status = status
                await s.flush()
                return arr

            async def invoice(arr, *, amount=2_500.0, value=1_000_000, group=None, status="draft", start=Q3[0],
                              householded=False):
                inv = Invoice(
                    firm_id=firm.id, run_id=run_row.id, kind="invoice", status=status, arrangement_id=arr.id,
                    mandate_id=arr.mandate_id, mandate_name="M", period_start=start, period_end=Q3[1],
                    amount=amount, currency="NZD", checks=[],
                    calculation={"billable_value": value, "group_value": group, "periods_per_year": 4,
                                 "period_fraction": 1.0, "householded": householded})
                s.add(inv)
                await s.flush()
                return inv

            print("\n=== An honest bill raises no block ===")
            arr = await arrangement()
            inv = await invoice(arr)                  # 1,000,000 -> 10,000 a year -> 2,500 a quarter
            found = await reconcile_invoice(s, inv)
            check("nothing blocks it", blocks(found), set())
            check("every finding is tagged as reconciliation", all(c["source"] == "reconcile" for c in found), True)

            print("\n=== A fee that does not match its schedule ===")
            inv = await invoice(await arrangement(), amount=2_600.0)
            check("a bill 100 over the schedule is blocked", "fee_does_not_match_schedule" in blocks(await reconcile_invoice(s, inv)), True)
            inv = await invoice(await arrangement(), amount=2_500.01)
            check("a cent of rounding is tolerated", "fee_does_not_match_schedule" in blocks(await reconcile_invoice(s, inv)), False)
            arr = await arrangement()
            inv = await invoice(arr, amount=1_312.50, value=600_000, group=2_000_000, householded=True)
            check("a household member's share of the combined fee is not a mismatch",
                  "fee_does_not_match_schedule" in blocks(await reconcile_invoice(s, inv)), False)   # 17,500 x 30% / 4

            print("\n=== The agreement itself ===")
            check("an ended arrangement blocks",
                  "agreement_ended" in blocks(await reconcile_invoice(s, await invoice(await arrangement(status="ended")))), True)
            check("an unconfirmed one blocks",
                  "agreement_unconfirmed" in blocks(await reconcile_invoice(s, await invoice(await arrangement(confirmed_by=None)))), True)
            check("the same person setting and confirming blocks",
                  "dual_control_broken" in blocks(await reconcile_invoice(
                      s, await invoice(await arrangement(set_by="Pat@x", confirmed_by="pat@x")))), True)
            check("two different people is fine",
                  "dual_control_broken" in blocks(await reconcile_invoice(s, await invoice(await arrangement()))), False)
            gone = await invoice(await arrangement())
            gone.arrangement_id = None
            check("a bill with no arrangement behind it blocks", blocks(await reconcile_invoice(s, gone)), {"agreement_missing"})

            print("\n=== A schedule edited after sign-off ===")
            future = datetime.now(timezone.utc) + timedelta(days=1)
            check("confirmed before the schedule was last edited: warned, not blocked",
                  warns(await reconcile_invoice(s, await invoice(await arrangement(confirmed_at=LONG_AGO)))) >=
                  {"schedule_changed_after_confirmation"}, True)
            check("confirmed after the last edit: no finding",
                  "schedule_changed_after_confirmation" in warns(await reconcile_invoice(
                      s, await invoice(await arrangement(confirmed_at=future)))), False)

            print("\n=== The same period twice ===")
            arr = await arrangement()
            await invoice(arr, status="issued")
            check("a second bill for an issued period is blocked",
                  "duplicate_period" in blocks(await reconcile_invoice(s, await invoice(arr))), True)
            other_period = await invoice(arr, start=date(2026, 4, 1))
            check("a different period is not a duplicate", "duplicate_period" in blocks(await reconcile_invoice(s, other_period)), False)

            print("\n=== A first bill that bears no relation to the agreed balance ===")
            check("agreed on 200,000, first billed on 1,000,000: warned",
                  "first_bill_balance_differs" in warns(await reconcile_invoice(
                      s, await invoice(await arrangement(agreed=200_000)))), True)
            check("agreed on the balance billed: no finding",
                  "first_bill_balance_differs" in warns(await reconcile_invoice(
                      s, await invoice(await arrangement(agreed=1_000_000)))), False)
            check("within 25%: no finding",
                  "first_bill_balance_differs" in warns(await reconcile_invoice(
                      s, await invoice(await arrangement(agreed=900_000)))), False)
            arr = await arrangement(agreed=200_000)
            await invoice(arr, status="issued", start=date(2026, 4, 1))
            check("it is only a first-bill check",
                  "first_bill_balance_differs" in warns(await reconcile_invoice(s, await invoice(arr))), False)
            check("no agreed balance on file: nothing to compare",
                  "first_bill_balance_differs" in warns(await reconcile_invoice(s, await invoice(await arrangement()))), False)

            print("\n=== A schedule that looks like a typo ===")
            arr = await arrangement(schedule=wild)
            found = await reconcile_invoice(s, await invoice(arr, amount=12_500.0))   # 500bps of 1m / 4
            check("a 500 bps effective rate is flagged", "rate_outside_expected_range" in warns(found), True)

            print("\n=== Re-checking replaces earlier findings, keeps the calculation's own ===")
            before = [{"code": "stale_valuation", "severity": "block", "detail": "x"},
                      {"code": "old_finding", "severity": "warn", "detail": "y", "source": "reconcile"}]
            merged = merge_checks(before, [{"code": "new_finding", "severity": "warn", "detail": "z", "source": "reconcile"}])
            check("the calculation's check survives", "stale_valuation" in {c["code"] for c in merged}, True)
            check("the old reconciliation finding is replaced", [c["code"] for c in merged if c.get("source") == "reconcile"], ["new_finding"])
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit


def main() -> int:
    asyncio.run(run())
    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
