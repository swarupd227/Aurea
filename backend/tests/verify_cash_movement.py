"""Prove money leaves only on a custodian's paper, from a different person, with a callback for a third party; that
cash spoken for is held back from every purchase; and that phasing releases one stage at a time.

Runs against a throwaway SQLite database.

    python -m tests.verify_cash_movement
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import cash_movement as cm, firm_time, reconciliation
from app.aurea_core.rebalancing import Position, optimise
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.cash_movement import CashRequest, PhaseInPlan, StandingInstruction
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, cm.CashMovementError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="USD", jurisdiction="US")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="o@t", full_name="O", role=UserRole.OPERATIONS, hashed_password="x")
            s.add_all([adviser, ops])
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            s.add(h)
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee", date_of_birth=date(1950, 1, 1))
            s.add(ann)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann IRA", currency="USD", cash_balance=20000, registration_type="traditional_ira")
            i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="AAA", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add_all([a, i])
            await s.flush()
            s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=10, currency="USD", source="test", is_real=True))
            hold = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=i.id, quantity=10000, market_value=100000, cost_basis=50000)
            s.add(hold)
            await s.flush()
            s.add(TaxLot(firm_id=firm.id, holding_id=hold.id, quantity=10000, cost_per_unit=5, acquired_on=today - timedelta(days=900)))
            await s.flush()
            ex, run = ToolExecutors(s, adviser.id, adviser.role, firm.id), ToolExecutors(s, ops.id, ops.role, firm.id)
            outsider = ToolExecutors(s, adviser.id, adviser.role, other.id)

            async def card(tool, inputs, executor=ex):
                return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

            async def count(model):
                return (await s.execute(select(func.count()).select_from(model))).scalar()

            print("\n=== Asking for a payment ===")
            ask = {"account_id": str(a.id), "kind": "withdrawal", "amount": 15000, "payee": "Ann Lee", "reason": "Kitchen renovation"}
            shown = await card("request_cash_movement", ask)
            check("the card names it, and the cash that is there", (shown["Amount"], shown["To"], "20,000" in shown["Cash there"], shown["Then"]), ("$15,000", "Ann Lee", True, "Held back from purchases, nothing paid"))
            await refused("no amount", ex.preview_change("request_cash_movement", {**ask, "amount": 0}), "above zero")
            await refused("an unknown kind", ex.preview_change("request_cash_movement", {**ask, "kind": "gift"}), "kind must be")
            await refused("no payee", ex.preview_change("request_cash_movement", {**ask, "payee": ""}), "who it is being paid")
            await refused("another firm's account", outsider.preview_change("request_cash_movement", ask), "not found")
            check("showing it writes nothing", await count(CashRequest), 0)
            read = await ex.request_cash_movement(ask)
            first = read["requests"][0]
            check("it opens, and moves nothing", (first["status"], float(a.cash_balance)), ("requested", 20000.0))
            check("the cash is held back", (await cm.held_back(s, firm, m))["total"], 15000.0)
            await refused("a direct buy cannot spend it", ex.preview_change("execute_orders", {"mandate_id": str(m.id), "reason": "Test", "orders": [{"side": "buy", "symbol": "AAA", "quantity": 1000}]}), "does not hold the cash")
            check("a smaller one can spend what is free", "Order 1" in await card("execute_orders", {"mandate_id": str(m.id), "reason": "Test", "orders": [{"side": "buy", "symbol": "AAA", "quantity": 100}]}), True)
            check("selling is not stopped", "Order 1" in await card("execute_orders", {"mandate_id": str(m.id), "reason": "Test", "orders": [{"side": "sell", "symbol": "AAA", "quantity": 100}]}), True)
            shown = await card("request_cash_movement", {**ask, "amount": 10000})
            check("a second one shows what is short", ("15,000" in shown["Already asked for"], "5,000" in shown["Short by"]), (True, True))

            print("\n=== The rebalancer holds it back too ===")
            positions = [Position(holding_id="h", instrument_id="x", symbol="X", name="X", asset_class="equity", market_value=10000, price=10, cost_basis=10000, account_id="a", custodian="c")]
            free = optimise(positions=positions, target_weights={"equity": 1.0}, cash=5000, drift_band=0.01)
            kept = optimise(positions=positions, target_weights={"equity": 1.0}, cash=5000, drift_band=0.01, held_back=4000)
            bought = lambda r: sum(o.quantity * o.est_price for o in r.orders if o.side == "buy")  # noqa: E731
            check("it buys less", (bought(kept) < bought(free), bought(kept) <= 1000.01), (True, True))

            print("\n=== Recording it paid ===")
            paid = {"request_id": first["id"], "paid_on": today.isoformat(), "source_document": "Custodian payment 7781"}
            await refused("not by the person who asked", ToolExecutors(s, adviser.id, UserRole.OPERATIONS, firm.id).preview_change("record_cash_paid", paid), "cannot also record")
            shown = await card("record_cash_paid", paid, run)
            check("the card names it and the cash after", (shown["Amount"], "5,000" in shown["Cash then"], shown["Document"]), ("$15,000", True, "Custodian payment 7781"))
            await refused("no paper", run.preview_change("record_cash_paid", {**paid, "source_document": ""}), "custodian confirmation")
            await refused("a date to come", run.preview_change("record_cash_paid", {**paid, "paid_on": (today + timedelta(days=1)).isoformat()}), "before it has happened")
            await run.record_cash_paid(paid)
            check("only now does the cash go", float(a.cash_balance), 5000.0)
            txn = (await s.execute(select(Transaction).where(Transaction.txn_type == "withdrawal"))).scalar_one()
            check("on the ledger", (float(txn.amount), txn.lineage["kind"]), (15000.0, "withdrawal"))
            check("nothing is held back now", (await cm.held_back(s, firm, m))["total"], 0.0)
            book = await reconciliation.book_as_of(s, a, today - timedelta(days=1))
            check("the book rolls back past it", (float(book["cash"]), book["cash_comparable"]), (20000.0, True))
            await refused("not twice", run.preview_change("record_cash_paid", paid), "already paid")

            print("\n=== A third party ===")
            third = await ex.request_cash_movement({**ask, "amount": 1000, "payee": "A Builder Ltd", "third_party": True})
            rid = next(r["id"] for r in third["requests"] if r["payee"] == "A Builder Ltd")
            await refused("no callback", run.preview_change("record_cash_paid", {"request_id": rid, "paid_on": today.isoformat(), "source_document": "Custodian payment 7782"}), "callback")
            shown = await card("record_cash_paid", {"request_id": rid, "paid_on": today.isoformat(), "source_document": "Custodian payment 7782", "callback_number": "+1 555 0100"}, run)
            check("with one, it is on the card", shown["Callback"], "+1 555 0100")
            big = await ex.request_cash_movement({**ask, "amount": 90000, "payee": "Ann Lee"})
            short = next(r for r in big["requests"] if r["amount"] == 90000.0)
            check("a request past the cash says how short", short["short_by"], 86000.0)
            await refused("and cannot be recorded", run.preview_change("record_cash_paid", {"request_id": short["id"], "paid_on": today.isoformat(), "source_document": "Custodian payment 7783"}), "raise the cash first")
            await refused("a cancel needs its reason", ex.preview_change("cancel_cash_request", {"request_id": short["id"], "reason": ""}), "say why")
            await ex.cancel_cash_request({"request_id": short["id"], "reason": "Client changed their mind"})
            check("it releases the cash", (await cm.held_back(s, firm, m))["total"], 1000.0)
            await ex.cancel_cash_request({"request_id": rid, "reason": "Paid by card instead"})

            print("\n=== Required distributions ===")
            read = await ex.read_cash_movements({})
            r = read["rmds"][0]
            check("one is required", (r["account"], r["taken"], r["left"] == r["required"]), ("Ann IRA", 0.0, True))
            taxable = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann taxable", currency="USD", cash_balance=100)
            s.add(taxable)
            await s.flush()
            await refused("not on an account with none", ex.preview_change("request_cash_movement", {**ask, "kind": "rmd", "account_id": str(taxable.id)}), "no required distribution")
            req = await ex.request_cash_movement({**ask, "kind": "rmd", "amount": 1000, "reason": "This year's RMD"})
            rid = next(x["id"] for x in req["requests"] if x["kind"] == "rmd")
            await run.record_cash_paid({"request_id": rid, "paid_on": today.isoformat(), "source_document": "Custodian payment 7790"})
            check("what is taken counts", (await ex.read_cash_movements({}))["rmds"][0]["taken"], 1000.0)

            print("\n=== A standing instruction ===")
            order = {"account_id": str(a.id), "payee": "Ann Lee", "amount": 500, "frequency": "monthly", "first_due": (today - timedelta(days=40)).isoformat(), "source_document": "Signed SLOA 2026"}
            shown = await card("record_standing_instruction", order)
            check("the card names it", (shown["To"], shown["Amount"]), ("Ann Lee", "$500 monthly"))
            await refused("an unknown frequency", ex.preview_change("record_standing_instruction", {**order, "frequency": "daily"}), "frequency must be")
            await refused("no form", ex.preview_change("record_standing_instruction", {**order, "source_document": ""}), "signed form")
            check("showing it writes nothing", await count(StandingInstruction), 0)
            read = await ex.record_standing_instruction(order)
            check("its next date is ahead", date.fromisoformat(read["standing"][0]["next_due"]) >= today, True)
            await ex.end_standing_instruction({"standing_id": read["standing"][0]["id"]})
            check("it can end", (await ex.read_cash_movements({}))["standing"], [])

            print("\n=== Phasing in ===")
            phase = {"mandate_id": str(m.id), "amount": 3000, "tranches": 3, "every_months": 1, "reason": "Inherited money, going in over a quarter"}
            shown = await card("start_phase_in", phase)
            check("the card names the stages, and places nothing", (shown["Stages"], shown["Orders"]), ("3 of $1,000, every 1 month(s)", "None placed"))
            await refused("one stage is not phasing", ex.preview_change("start_phase_in", {**phase, "tranches": 1}), "between 2 and 36")
            await refused("more than the cash", ex.preview_change("start_phase_in", {**phase, "amount": 999999}), "cannot phase in")
            await refused("another firm's mandate", outsider.preview_change("start_phase_in", phase), "not found")
            check("showing it writes nothing", await count(PhaseInPlan), 0)
            read = await ex.start_phase_in(phase)
            check("the first stage goes today, the rest wait", ((await cm.held_back(s, firm, m))["phasing"], read["phasing"][0]["left"]), (2000.0, 2))
            plan = (await s.execute(select(PhaseInPlan))).scalar_one()
            check("as dates pass they release", (cm.tranches_left(plan, today + timedelta(days=32)), cm.tranches_left(plan, today + timedelta(days=95))), (1, 0))
            await refused("one plan at a time", ex.preview_change("start_phase_in", phase), "already has a plan")
            shown = await card("end_phase_in", {"plan_id": str(plan.id)})
            check("ending names what is released", "2,000" in shown["Released"], True)
            await ex.end_phase_in({"plan_id": str(plan.id)})
            check("and it is", (await cm.held_back(s, firm, m))["phasing"], 0.0)

            print("\n=== Holds and scope ===")
            m.exit_hold = "deceased"
            await refused("nothing is paid from a dead client's account", ex.preview_change("request_cash_movement", ask), "no executor")
            m.exit_hold = None
            check("another firm sees none of it", (await outsider.read_cash_movements({}))["requests"], [])
            check("one household's", len((await ex.read_cash_movements({"household_id": str(h.id)}))["rmds"]), 1)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
