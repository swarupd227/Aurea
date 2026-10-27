"""Prove declared valuations for private holdings (L100 §9): a stated value needs its source
document, cannot rewrite history, explains a large move, revalues what it touches, is named
"declared" - neither real nor synthetic - everywhere it is read, and is current for a quarter
where a listed price is current for a month.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_valuations
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.api import valuations as valuations_api
from app.aurea_core import billing, performance, pricing, valuations
from app.aurea_core.analytics import risk_data
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm
from app.models.valuations import ValuationStatement

PASS, FAIL = [], []
DOC = "Q3 2026 capital account statement - Southern Cross Private Equity Fund I"


def check(label: str, got, want, tol: float = 0.0001) -> None:
    ok = (abs(float(got) - float(want)) < tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(coro, fragment: str) -> bool:
    try:
        await coro
        return False
    except valuations.ValuationError as exc:
        return fragment.lower() in str(exc).lower()


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date(2026, 10, 27)

        print("\n=== Helpers ===")
        check("a declared valuation is its own basis", pricing.price_basis("declared_valuation", False), "declared")
        check("a real quote is market", pricing.price_basis("yahoo", True), "market")
        check("an invented one is synthetic", pricing.price_basis("synthetic", False), "synthetic")

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            s.add(firm)
            await s.flush()
            ppef = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="PPEF1", name="Southern Cross PE I",
                              asset_class="alternatives", market_type=MarketType.PRIVATE, currency="NZD")
            listed = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="LIST", name="Listed",
                                asset_class="equity", market_type=MarketType.PUBLIC, currency="NZD")
            s.add_all([ppef, listed])
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=ppef.id, as_of=date(2026, 1, 1), close=100.0, currency="NZD",
                        source="synthetic", is_real=False))
            s.add(Price(firm_id=firm.id, instrument_id=listed.id, as_of=today - timedelta(days=3), close=10.0,
                        currency="NZD", source="yahoo", is_real=True))
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="H")
            s.add(hh)
            await s.flush()
            person = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="P")
            s.add(person)
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="M",
                              mandate_type="advisory", is_active=True)
            s.add(mandate)
            await s.flush()
            account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A", currency="NZD",
                              cash_balance=Decimal("0"))
            s.add(account)
            await s.flush()
            h_ppef = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=ppef.id,
                             quantity=Decimal("100"), market_value=Decimal("10000"), cost_basis=Decimal("10000"))
            h_list = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=listed.id,
                             quantity=Decimal("1000"), market_value=Decimal("10000"), cost_basis=Decimal("10000"))
            s.add_all([h_ppef, h_list])
            await s.flush()

            async def rec(value, as_of, **kw):
                return await valuations.record_valuation(
                    s, firm.id, instrument_id=ppef.id, as_of=as_of, unit_value=value,
                    source_document=kw.pop("doc", DOC), recorded_by=kw.pop("by", "ops@t"), today=today, **kw)

            print("\n=== Before: a placeholder, not a valuation ===")
            before = (await valuations.private_valuations(s, firm.id, today=today))[0]
            check("it is carried on a synthetic price", before["basis"], "synthetic")
            check("so it is flagged as needing a stated valuation", before["overdue"], True)
            check("and the book rests on it", (before["holdings"], before["value_held"]), (1, 10000.0))

            print("\n=== Each rule is a refusal ===")
            check("no document: refused", await refused(rec(105, date(2026, 9, 30), doc=" "), "source document"), True)
            check("a one-word document is not evidence", await refused(rec(105, date(2026, 9, 30), doc="n/a"), "source document"), True)
            check("a listed security cannot be hand-priced", await refused(valuations.record_valuation(
                s, firm.id, instrument_id=listed.id, as_of=date(2026, 9, 30), unit_value=11, source_document=DOC,
                recorded_by="x", today=today), "listed security"), True)
            check("a future date is refused", await refused(rec(105, today + timedelta(days=1)), "future"), True)
            check("zero is refused", await refused(rec(0, date(2026, 9, 30)), "greater than zero"), True)
            check("another firm's instrument is not found", await refused(valuations.record_valuation(
                s, uuid.uuid4(), instrument_id=ppef.id, as_of=date(2026, 9, 30), unit_value=105, source_document=DOC,
                recorded_by="x", today=today), "not found"), True)
            check("a +80% move without a note is refused, and says so", await refused(
                rec(180, date(2026, 9, 30)), "+80%"), True)
            n_stmts = (await s.execute(select(func.count()).select_from(ValuationStatement))).scalar_one()
            check("nothing was written by any refusal", n_stmts, 0)

            print("\n=== A valuation is recorded, with its move, and revalues what it touches ===")
            r = await rec(102.5, date(2026, 9, 30))
            check("recorded at the stated value", (r["unit_value"], r["as_of"]), (102.5, "2026-09-30"))
            check("against the placeholder it replaced", (r["previous"]["value"], r["previous"]["basis"]), (100.0, "synthetic"))
            check("a 2.5% move", r["change_pct"], 0.025)
            check("one holding revalued", r["holdings_revalued"], 1)
            check("100 units: 10,000 to 10,250", (r["value_before"], r["value_after"]), (10000.0, 10250.0))
            await s.refresh(h_ppef)
            check("the holding really carries the new value", float(h_ppef.market_value), 10250.0)
            check("its lineage names the basis", h_ppef.lineage["price_basis"], "declared")
            check("and is not claimed to be a real market price", h_ppef.lineage["is_real_price"], False)
            row = (await s.execute(select(Price).where(Price.instrument_id == ppef.id, Price.as_of == date(2026, 9, 30)))).scalar_one()
            check("the price row is marked declared and not real - so no fill can ever use it",
                  (row.source, row.is_real), ("declared_valuation", False))
            stmt = (await s.execute(select(ValuationStatement).where(ValuationStatement.status == "active"))).scalar_one()
            check("the statement keeps who and from what", (stmt.recorded_by, stmt.source_document), ("ops@t", DOC))
            check("and what it replaced", (float(stmt.previous_value), stmt.previous_as_of), (100.0, date(2026, 1, 1)))
            await s.refresh(h_list)
            check("a listed holding is not disturbed", float(h_list.market_value), 10000.0)

            print("\n=== History is not rewritten ===")
            check("a valuation dated before the latest is refused",
                  await refused(rec(101, date(2026, 6, 30)), "already on record"), True)
            again = await rec(103.0, date(2026, 9, 30))
            check("the same date supersedes, and compares against what came BEFORE it",
                  (again["previous"]["value"], again["change_pct"]), (100.0, 0.03))
            statuses = sorted(x.status for x in (await s.execute(select(ValuationStatement))).scalars().all())
            check("the replaced statement is kept, marked superseded", statuses, ["active", "superseded"])
            check("still one price row for that date",
                  (await s.execute(select(func.count()).select_from(Price).where(
                      Price.instrument_id == ppef.id, Price.as_of == date(2026, 9, 30)))).scalar_one(), 1)
            big = await rec(180, date(2026, 10, 15), notes="Fund realised its largest holding at a 60% premium.")
            check("a large move WITH a note is accepted", big["change_pct"], round(180 / 103.0 - 1, 4))

            print("\n=== It is current for a quarter, not a month ===")
            now = (await valuations.private_valuations(s, firm.id, today=today))[0]
            check("declared and recent: not overdue", (now["basis"], now["overdue"]), ("declared", False))
            old = (await valuations.private_valuations(s, firm.id, today=today + timedelta(days=120)))[0]
            check("120 days on it is overdue against the 100-day limit", old["overdue"], True)

            print("\n=== Billing: its own limit, its own label ===")
            sched = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="F", name="Flat", fee_type="flat_bps",
                                flat_bps=Decimal("100"), tiers=[], currency="NZD")
            s.add(sched)
            await s.flush()
            await billing.activate_arrangement(
                s, firm_id=firm.id, mandate=mandate, fee_schedule_id=sched.id, billing_method="arrears",
                billing_frequency="quarterly", householding=False, effective_from=date(2020, 1, 1),
                set_by="a@x", set_at=None, confirmed_by="b@x", confirmed_at=date(2020, 1, 2), source="seed")
            await s.flush()

            def codes(pv):
                e = pv["arrangements"][0]
                return {c["code"]: c["severity"] for c in e["checks"]}, e

            c, e = codes(await billing.preview(s, firm.id, as_of=today))
            check("a recent declared valuation does not block", "stale_valuation" in c, False)
            check("it is named, as a warning", c.get("declared_valuation"), "warn")
            check("it is not called synthetic", "synthetic_price" in c, False)
            check("the symbol is on the bill", e["valuation"]["declared_price_symbols"], ["PPEF1"])
            c, e = codes(await billing.preview(s, firm.id, as_of=today + timedelta(days=60)))
            check("a listed price 63 days old blocks at its 35-day limit, and says which",
                  ("stale_valuation" in c, "LIST" in next(x["detail"] for x in e["checks"] if x["code"] == "stale_valuation")), (True, True))

            print("\n=== Performance and data quality name it ===")
            await s.refresh(h_ppef)
            perf = await performance.account_performance(s, account.id, firm_id=firm.id)
            check("the instrument is named declared in the disclosures", perf["disclosures"]["declared_price_symbols"], ["PPEF1"])
            check("and, honestly, also synthetic: its January placeholder is a point the series uses",
                  "PPEF1" in perf["disclosures"]["synthetic_price_symbols"], True)

            # A fund whose whole history is stated valuations is declared and NOT synthetic.
            fund2 = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="PPEF2", name="Fund II",
                               asset_class="alternatives", market_type=MarketType.PRIVATE, currency="NZD")
            s.add(fund2)
            acct2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A2", currency="NZD",
                            cash_balance=Decimal("0"))
            s.add(acct2)
            await s.flush()
            s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct2.id, instrument_id=fund2.id,
                          quantity=Decimal("10"), market_value=Decimal("1040"), cost_basis=Decimal("1000")))
            await s.flush()
            for d, v in ((date(2026, 8, 31), 100.0), (date(2026, 9, 30), 102.0), (date(2026, 10, 15), 104.0)):
                await valuations.record_valuation(s, firm.id, instrument_id=fund2.id, as_of=d, unit_value=v,
                                                  source_document=DOC, recorded_by="ops@t", today=today)
            p2 = await performance.account_performance(s, acct2.id, firm_id=firm.id)
            check("a stated-valuations-only fund is declared", p2["disclosures"]["declared_price_symbols"], ["PPEF2"])
            check("and not synthetic", p2["disclosures"]["synthetic_price_symbols"], [])
            check("it is priced, not held flat", p2["disclosures"]["unpriced_flat_symbols"], [])
            check("its return is what the statements say: +2% then +1.96%", p2["twr"], round(1.02 * (104 / 102) - 1, 4))

            # data quality uses the real calendar
            real_today = date.today()
            s.add(Price(firm_id=firm.id, instrument_id=listed.id, as_of=real_today - timedelta(days=1), close=10.5,
                        currency="NZD", source="yahoo", is_real=True))
            s.add(Price(firm_id=firm.id, instrument_id=ppef.id, as_of=real_today - timedelta(days=60), close=190.0,
                        currency="NZD", source="declared_valuation", is_real=False))
            await s.flush()
            dq = (await risk_data.compute(s, firm.id, []))["data_quality"]
            check("a 60-day-old stated value is current for a private fund", dq["timeliness"], 1.0)
            s.add(Price(firm_id=firm.id, instrument_id=ppef.id, as_of=real_today - timedelta(days=59), close=191.0,
                        currency="NZD", source="declared_valuation", is_real=False))
            await s.flush()
            check("accuracy counts only real market prices (1 of 3 instruments)", dq["accuracy"], round(1 / 3, 3), tol=0.001)

            print("\n=== Who may record one ===")
            check("the tool pauses for confirmation", TOOLS["record_valuation"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("operations and the portfolio team hold it",
                  TOOLS["record_valuation"].roles_required, {UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})
            check("reading it is not a state change", TOOLS["read_private_valuations"].change_state, ToolChangeState.NO)
            user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, email="admin@t")
            try:
                await valuations_api.record(valuations_api.ValuationIn(
                    instrument_id=ppef.id, unit_value=195, as_of=today, source_document=DOC), user=user, firm=firm, db=s)
                check("an administrator is refused by the API", "recorded", "refused")
            except HTTPException as exc:
                check("an administrator is refused by the API", exc.status_code, 403)
            ex_adv = ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, firm.id)
            try:
                await ex_adv.execute("record_valuation", {"symbol": "PPEF1", "unit_value": 195, "as_of": "2026-10-27",
                                                          "source_document": DOC})
                check("an adviser is refused by the tool", "recorded", "refused")
            except ExecutorError:
                check("an adviser is refused by the tool", "refused", "refused")
            listing = await ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, firm.id).execute("read_private_valuations", {})
            check("but may read the valuations of both funds", [i["symbol"] for i in listing["instruments"]], ["PPEF1", "PPEF2"])
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit

    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
