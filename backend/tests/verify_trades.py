"""Prove the blotter says what happened and when it settles, that a direct order leaves a record and must give its reason,
that capacity for loss stops it and concentration is named, and that a tax summary reads the book and is never a form.

Runs against a throwaway SQLite database.

    python -m tests.verify_trades
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import blotter, firm_time, markets, tax_documents
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tax_documents import TaxDocument
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, tax_documents.TaxDocumentError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    print("\n=== Settlement dates ===")
    us, nz = markets.get("US"), markets.get("NZ")
    check("a US Monday settles Tuesday", blotter.settlement_date(date(2026, 11, 2), us), date(2026, 11, 3))
    check("a US Friday settles Monday", blotter.settlement_date(date(2026, 10, 30), us), date(2026, 11, 2))
    check("a T+2 Thursday settles Monday", blotter.settlement_date(date(2026, 10, 29), nz), date(2026, 11, 2))

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="USD", jurisdiction="US")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            s.add(h)
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee")
            s.add(ann)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory", suitability={"capacity_for_loss": "low"}, constraints={})
            s.add(m)
            await s.flush()
            a1 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann One", currency="USD", cash_balance=100000)
            s.add(a1)
            m2 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann second", mandate_type="advisory", suitability={"capacity_for_loss": "high"}, constraints={})
            s.add(m2)
            await s.flush()
            a2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m2.id, name="Ann Two", currency="USD", cash_balance=100000)
            eq = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="AAA", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            fund = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="PVT", name="Private Fund", asset_class="alternatives", market_type=MarketType.PRIVATE, currency="USD")
            s.add_all([a2, eq, fund])
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=eq.id, as_of=today, close=10, currency="USD", source="test", is_real=True))
            bond = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BND", name="BND", asset_class="fixed_income", market_type=MarketType.PUBLIC, currency="USD")
            s.add(bond)
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=bond.id, as_of=today, close=10, currency="USD", source="test", is_real=True))
            await s.flush()
            ex, outsider = ToolExecutors(s, adviser.id, adviser.role, firm.id), ToolExecutors(s, adviser.id, adviser.role, other.id)

            def order(mandate, *lines, reason="Top up"):
                return {"mandate_id": str(mandate.id), "reason": reason, "orders": [{"side": x, "symbol": y, "quantity": q} for x, y, q in lines]}

            async def card(tool, inputs):
                return {r["label"]: r["value"] for r in (await ex.preview_change(tool, inputs))["rows"]}

            print("\n=== Pre-trade ===")
            await refused("no reason", ex.preview_change("execute_orders", order(m, ("buy", "BND", 1), reason="")), "give the reason")
            await refused("past the capacity ceiling", ex.preview_change("execute_orders", order(m, ("buy", "AAA", 8000))), "ceiling for low capacity")
            check("under it goes ahead", "Order 1" in await card("execute_orders", order(m, ("buy", "AAA", 1000))), True)
            shown = await card("execute_orders", order(m2, ("buy", "AAA", 3000)))
            check("a position past a fifth is named, not refused", shown["Concentration"], "AAA 30.0% of the book")
            check("a smaller one is not", "Concentration" in await card("execute_orders", order(m2, ("buy", "AAA", 1000))), False)

            check("a bond fund is not a concentration", "Concentration" in await card("execute_orders", order(m2, ("buy", "BND", 3000))), False)

            print("\n=== A direct order leaves a record ===")
            before = (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar()
            await ex.execute_orders(order(m, ("buy", "AAA", 100), reason="Client asked"))
            await ex.execute_orders(order(m2, ("buy", "AAA", 200), reason="Client asked"))
            rows = (await s.execute(select(LedgerEntry).where(LedgerEntry.event_type == "direct_order_placed"))).scalars().all()
            check("each is on the ledger, with who and why", (len(rows), rows[0].actor, rows[0].content["reason"]), (2, "a@t", "Client asked"))
            check("nothing else is lost", (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar() >= before + 2, True)

            print("\n=== The blotter ===")
            read = await ex.read_trades({})
            t = read["trades"][0]
            check("two filled, both direct", (read["summary"]["filled"], read["summary"]["direct"]), (2, 2))
            check("each has a confirmation and settlement date", (t["confirmation"].startswith("TC-"), t["settles_on"]), (True, blotter.settlement_date(firm_time.today(firm), us).isoformat()))
            check("the net is there", t["net"] >= t["gross"], True)
            check("unsettled until its date", read["summary"]["unsettled"], 2)
            b = read["blocks"][0]
            check("across two accounts it is a block", (b["symbol"], b["accounts"], b["units"], b["same_price"]), ("AAA", 2, 300.0, True))
            check("a paper venue says so", ("no market was reached" in read["message"], read["paper_venue"]), (True, True))
            check("one household's", len((await ex.read_trades({"household_id": str(h.id)}))["trades"]), 2)
            check("another firm sees none", (await outsider.read_trades({}))["trades"], [])

            print("\n=== A tax year ===")
            year = today.year - 1
            s.add_all([
                Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=eq.id, txn_type="sell", quantity=100, price=10, amount=1000, trade_date=date(year, 6, 1),
                            lineage={"fees": 0, "lots": [{"acquired_on": date(year - 3, 1, 1).isoformat(), "quantity": 60, "cost": 300}, {"acquired_on": date(year, 1, 1).isoformat(), "quantity": 40, "cost": 500}]}),
                Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=eq.id, txn_type="sell", quantity=1, price=10, amount=10, trade_date=date(year, 7, 1), lineage={}),
                Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=eq.id, txn_type="dividend", quantity=100, price=1, amount=100, trade_date=date(year, 8, 1), lineage={}),
                Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=None, txn_type="fee", quantity=0, price=0, amount=250, trade_date=date(year, 9, 1), lineage={}),
                Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=eq.id, txn_type="sell", quantity=1, price=10, amount=10, trade_date=date(year - 1, 7, 1), lineage={}),
            ])
            a2.registration_type = "traditional_ira"
            s.add(Transaction(firm_id=firm.id, account_id=a2.id, instrument_id=None, txn_type="withdrawal", quantity=0, price=0, amount=5000, trade_date=date(year, 3, 1), lineage={"kind": "rmd"}))
            s.add(Holding(firm_id=firm.id, account_id=a1.id, instrument_id=fund.id, quantity=10, market_value=1000, cost_basis=1000))
            await s.flush()
            read = await ex.read_tax_documents({"household_id": str(h.id)})
            one = next(x for x in read["summaries"] if x["account"] == "Ann One")
            check("gains split at a year's holding", (one["long"], one["short"]), (300.0, -100.0))
            check("a sale with no lots is counted and named", (one["proceeds"], one["no_lots"]), (1010.0, 1))
            check("income and fees are there", (one["dividends"], one["fees"]), (100.0, 250.0))
            check("only that year", one["trades"], 2)
            kinds = sorted((e["kind"], e["issuer"]) for e in read["expected"])
            check("what the year should produce", kinds, [("1099_composite", "Ann One"), ("1099_r", "Ann Two"), ("k1", "Private Fund")])
            check("it says it is not the form", (read["not_the_official_form"], "not the official form" in read["message"]), (True, True))
            k1 = next(e for e in read["expected"] if e["kind"] == "k1")
            check("past its date it is overdue", k1["status"], "overdue" if today > date(year + 1, 3, 15) else "awaited")

            print("\n=== A tax year is the market's ===")
            india = markets.get("IN")
            check("the Indian year runs April to March", tax_documents._span(india, 2025)["label"], "2025-26")
            check("its form is due after it ends", tax_documents._due("capital_gains_statement", tax_documents._span(india, 2025)), date(2026, 6, 29))
            check("the last to end, from November", tax_documents._last_year(india, date(2026, 11, 2)), 2025)
            check("and from February", tax_documents._last_year(india, date(2026, 2, 2)), 2024)
            check("a US form keeps its date", tax_documents._due("k1", tax_documents._span(us, 2025)), date(2026, 3, 15))
            check("no market, a calendar year", tax_documents._span(None, 2025)["label"], "2025")

            print("\n=== One arrives ===")
            got = {"household_id": str(h.id), "kind": "k1", "tax_year": year, "issuer": "Private Fund", "source_document": "Client tax folder 2026"}
            shown = await card("record_tax_document", got)
            check("the card names it", (shown["Document"], shown["For"], shown["Year"]), ("Schedule K-1", "Private Fund", str(year)))
            await refused("a year to come", ex.preview_change("record_tax_document", {**got, "tax_year": today.year + 2}), "has not happened")
            await refused("no filing", ex.preview_change("record_tax_document", {**got, "source_document": ""}), "where it is filed")
            await refused("another firm's household", outsider.preview_change("record_tax_document", got), "not found")
            check("showing it writes nothing", (await s.execute(select(func.count()).select_from(TaxDocument))).scalar(), 0)
            read = await ex.record_tax_document(got)
            check("it reads as received", next(e for e in read["expected"] if e["kind"] == "k1")["status"], "received")
            await refused("not twice", ex.preview_change("record_tax_document", got), "already recorded")
            shown = await card("record_tax_document", {**got, "issuer": "A Trust"})
            check("one the book did not call for says so", "did not call" in shown["Was expected"], True)
            firmwide = await ex.read_tax_documents({})
            check("the firm's list counts it", (firmwide["summary"]["expected"], firmwide["summary"]["received"]), (3, 1))
            check("another firm sees none", (await outsider.read_tax_documents({}))["households"], [])
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
