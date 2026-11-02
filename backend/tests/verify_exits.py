"""Prove a client leaving, or dying, is recorded from a document, holds the mandate, values the book as it stood,
books only what a custodian moved, pays who a designation names, steps basis up only where the law does, and
closes only when nothing is left.

Runs against a throwaway SQLite database.

    python -m tests.verify_exits
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.drift_rebalancing import DriftRebalancingAgent
from app.atlas.base import AgentContext, Subject
from app.aurea_core import exits, firm_time, protection, reconciliation
from app.aurea_core.graph import household_brain, list_households
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AutonomyTier, MarketType, UserRole
from app.models.exits import AccountTransfer, ClientExit
from app.models.governance import AgentRun
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TaxLot, Transaction
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
    except (ExecutorError, exits.ExitError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:130]}")
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
            async def firm_of(slug, jurisdiction, currency):
                f = Firm(id=uuid.uuid4(), name=slug, slug=slug, base_currency=currency, jurisdiction=jurisdiction)
                s.add(f)
                await s.flush()
                return f

            async def household_of(firm, name):
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(h)
                await s.flush()
                return h

            async def person_of(firm, h, name, dob=None):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=name, date_of_birth=dob, profile={"tax": {"annual_income": 100000}})
                s.add(p)
                await s.flush()
                return p

            async def mandate_of(firm, person, name, *, cash=1000, registration=None, model=None):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name=name, mandate_type="advisory",
                            suitability={"risk_profile": "growth", "capacity_for_loss": "high"}, constraints={}, model_portfolio_id=model)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} A/C", currency=firm.base_currency, cash_balance=cash,
                            registration_type=registration, custodian="Custody Co")
                s.add(a)
                await s.flush()
                return m, a

            async def instrument(firm, symbol, prices, real=True):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class="equity", market_type=MarketType.PUBLIC, currency=firm.base_currency)
                s.add(i)
                await s.flush()
                for day, close in prices:
                    s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=day, close=close, currency=firm.base_currency, source="test" if real else "seed", is_real=real))
                await s.flush()
                return i

            async def holding(firm, account, inst, *lots, price=10):
                quantity = sum(q for q, _, _ in lots)
                h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=inst.id, quantity=quantity, market_value=quantity * price,
                            cost_basis=sum(q * c for q, c, _ in lots))
                s.add(h)
                await s.flush()
                for q, c, on in lots:
                    s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=q, cost_per_unit=c, acquired_on=on))
                await s.flush()
                return h

            async def card(executor, tool, inputs):
                preview = await executor.preview_change(tool, inputs)
                return {r["label"]: r["value"] for r in preview["rows"]}

            async def count(model, *where):
                return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar()

            async def lots_of(h):
                return (await s.execute(select(TaxLot).where(TaxLot.holding_id == h.id))).scalars().all()

            nz = await firm_of("nz", "NZ", "NZD")
            other = await firm_of("o", "NZ", "NZD")
            us = await firm_of("us", "US", "USD")
            india = await firm_of("in", "IN", "INR")
            adviser = User(id=uuid.uuid4(), firm_id=nz.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            ops = User(id=uuid.uuid4(), firm_id=nz.id, email="o@t", full_name="O", role=UserRole.OPERATIONS, hashed_password="x")
            s.add_all([adviser, ops])
            today = firm_time.today(nz)
            ago = lambda n: today - timedelta(days=n)  # noqa: E731
            ex, run = ToolExecutors(s, adviser.id, adviser.role, nz.id), ToolExecutors(s, ops.id, ops.role, nz.id)
            outsider = ToolExecutors(s, ops.id, ops.role, other.id)

            model = ModelPortfolio(id=uuid.uuid4(), firm_id=nz.id, name="Growth", drift_band=0.05)
            s.add(model)
            await s.flush()
            lee = await household_of(nz, "Lee")
            ann = await person_of(nz, lee, "Ann Lee")
            m1, a1 = await mandate_of(nz, ann, "Lee One", model=model.id)
            m2, a2 = await mandate_of(nz, ann, "Lee Two", cash=0)
            aaa = await instrument(nz, "AAA", [(ago(30), 8), (today, 10)])
            bbb = await instrument(nz, "BBB", [(today, 20)])
            seed = await instrument(nz, "SEED", [(today, 5)], real=False)
            h1 = await holding(nz, a1, aaa, (60, 6, ago(400)), (40, 9, ago(100)))
            await holding(nz, a2, bbb, (10, 15, ago(200)), price=20)
            await holding(nz, a1, seed, (10, 4, ago(300)), price=5)

            print("\n=== A client gives notice ===")
            leaving = {"household_id": str(lee.id), "reason": "Moving to another adviser", "source_document": "Client letter, 1 Nov 2026",
                       "receiving_institution": "Other Wealth"}
            shown = await card(ex, "start_client_departure", leaving)
            check("the card names who, why and where", (shown["Client"], shown["Scope"], shown["Reason"], shown["Going to"]),
                  ("Lee", "Whole household", "Moving to another adviser", "Other Wealth"))
            check("and what the book holds, leaving out what has no real price", ("2,200" in shown["Value then"], shown["No real price"]), (True, "SEED"))
            await refused("no reason", ex.preview_change("start_client_departure", {**leaving, "reason": ""}), "say why")
            await refused("no document", ex.preview_change("start_client_departure", {**leaving, "source_document": ""}), "which document")
            await refused("notice to come", ex.preview_change("start_client_departure", {**leaving, "notice_date": (today + timedelta(days=2)).isoformat()}), "before it has been given")
            await refused("a date it cannot read", ex.preview_change("start_client_departure", {**leaving, "notice_date": "soon"}), "YYYY-MM-DD")
            await refused("another firm's household", outsider.preview_change("start_client_departure", leaving), "not found")
            check("showing it writes nothing", (await count(ClientExit), m1.exit_hold), (0, None))
            done = await ex.start_client_departure(leaving)
            exit_id = done["id"]
            check("the mandates are held", (m1.exit_hold, m2.exit_hold), ("leaving", "leaving"))
            check("the book is kept", (done["value_then"], done["book_complete"]), (2200.0, False))
            await refused("a second on the same mandate", ex.preview_change("start_client_departure", leaving), "already open")
            refusal = exits.order_refusal(m1, [{"side": "buy"}])
            check("a held mandate may not buy", "nothing may be bought" in refusal, True)
            check("it may sell", exits.order_refusal(m1, [{"side": "sell"}]), None)
            await refused("a direct buy is refused", ex.preview_change("execute_orders", {"mandate_id": str(m1.id), "orders": [{"symbol": "AAA", "side": "buy", "quantity": 1}]}), "leaving the firm")
            sold = await card(ex, "execute_orders", {"mandate_id": str(m1.id), "orders": [{"symbol": "AAA", "side": "sell", "quantity": 1}]})
            check("a direct sale is not", "Sell 1 AAA" in sold["Order 1"], True)
            ar = AgentRun(firm_id=nz.id, agent_key="drift_rebalancing", status="sensing", tier="tier_2", trigger="manual", started_at=firm_time.utcnow())
            s.add(ar)
            await s.flush()
            ctx = AgentContext(session=s, firm=nz, run=ar, tier=AutonomyTier.TIER_2, guardrails={}, config={}, subject=Subject("mandate", m1.id, m1.name))
            check("the rebalancer stands down", (await DriftRebalancingAgent().sense(ctx))["applicable"], False)

            print("\n=== The custodian moves it ===")
            moving = {"exit_id": exit_id, "account_id": str(a1.id), "transfer_date": today.isoformat(), "source_document": "ACAT confirmation 7741"}
            shown = await card(run, "record_account_transfer", moving)
            check("the card names what moves and to whom", (shown["Account"], shown["To"], shown["Share"], "100 AAA" in shown["Positions"]),
                  ("Lee One A/C", "Other Wealth", "100.0%", True))
            check("and that the account closes", (shown["Account then"], shown["No real price"]), ("Closes", "SEED"))
            await refused("no document", run.preview_change("record_account_transfer", {**moving, "source_document": ""}), "which document")
            await refused("a date to come", run.preview_change("record_account_transfer", {**moving, "transfer_date": (today + timedelta(days=1)).isoformat()}), "before it has happened")
            await refused("before the notice", run.preview_change("record_account_transfer", {**moving, "transfer_date": ago(40).isoformat()}), "cannot be dated before")
            await refused("an account outside it", run.preview_change("record_account_transfer", {**moving, "account_id": str(uuid.uuid4())}), "not part of this")
            await refused("another firm's", outsider.preview_change("record_account_transfer", moving), "not found")
            s.add(Order(firm_id=nz.id, account_id=a1.id, instrument_id=aaa.id, mandate_id=m1.id, side="buy", quantity=1, status="placed", history=[], lineage={}))
            await s.flush()
            await refused("an open order", run.preview_change("record_account_transfer", moving), "order still open")
            await s.delete((await s.execute(select(Order))).scalar_one())
            await s.flush()
            check("showing it moves nothing", (float(h1.quantity), float(a1.cash_balance), await count(AccountTransfer)), (100.0, 1000.0, 0))
            await refused("while assets are held it will not close", run.preview_change("close_client_exit", {"exit_id": exit_id}), "still holding", "Lee One", "Lee Two")
            await run.record_account_transfer(moving)
            check("the holding, lots and cash come off", (float(h1.quantity), len(await lots_of(h1)), float(a1.cash_balance)), (0.0, 0, 0.0))
            check("the account closes", (a1.status, a1.closed_on), ("closed", today))
            check("each position and the cash is on the ledger", await count(Transaction, Transaction.txn_type == "transfer_out", Transaction.account_id == a1.id), 3)
            await refused("a closed account takes no more", run.preview_change("record_account_transfer", moving), "closed")
            before = await reconciliation.book_as_of(s, a1, ago(1))
            check("the book rolls back past a transfer", (float(before["positions"][aaa.id]), float(before["cash"]), before["cash_comparable"]), (100.0, 1000.0, True))
            await refused("once money has moved it cannot be withdrawn", ex.preview_change("withdraw_client_exit", {"exit_id": exit_id, "reason": "Stayed"}), "already moved")
            await run.record_account_transfer({**moving, "account_id": str(a2.id)})

            print("\n=== It closes ===")
            shown = await card(run, "close_client_exit", {"exit_id": exit_id})
            check("the card names what ends", (shown["Client"], shown["Mandates ending"], shown["Household"]), ("Lee", "2", "Leaves the client list"))
            check("still on the list until then", any(h["name"] == "Lee" for h in await list_households(s, nz.id)), True)
            closed = await run.close_client_exit({"exit_id": exit_id})
            check("it is complete", (closed["status"], closed["left_firm"] > 0, closed["stayed_at_firm"]), ("complete", True, 0.0))
            check("the mandates end and the hold comes off", (m1.is_active, m1.exit_hold), (False, None))
            check("the household leaves every scan", (lee.left_on, any(h["name"] == "Lee" for h in await list_households(s, nz.id))), (today, False))
            check("its record can still be read", (await household_brain(s, lee.id, firm_id=nz.id))["household"]["name"], "Lee")
            await refused("not closed twice", run.preview_change("close_client_exit", {"exit_id": exit_id}), "already complete")

            print("\n=== A client changes their mind ===")
            kim = await household_of(nz, "Kim")
            bo = await person_of(nz, kim, "Bo Kim")
            m3, a3 = await mandate_of(nz, bo, "Kim One")
            started = await ex.start_client_departure({"household_id": str(kim.id), "reason": "Thinking of moving", "source_document": "Email of 1 Nov"})
            await refused("a withdrawal needs its reason", ex.preview_change("withdraw_client_exit", {"exit_id": started["id"], "reason": ""}), "say why")
            shown = await card(ex, "withdraw_client_exit", {"exit_id": started["id"], "reason": "Decided to stay"})
            check("the card names it", (shown["Client"], shown["Kind"]), ("Kim", "Departure"))
            back = await ex.withdraw_client_exit({"exit_id": started["id"], "reason": "Decided to stay"})
            check("the hold comes off, and it stays on record", (m3.exit_hold, back["status"], back["cancel_reason"]), (None, "cancelled", "Decided to stay"))

            print("\n=== A death ===")
            ng = await household_of(nz, "Ng")
            mary, sam = await person_of(nz, ng, "Mary Ng"), await person_of(nz, ng, "Sam Ng")
            s.add(LegalEntity(id=uuid.uuid4(), firm_id=nz.id, household_id=ng.id, name="Ng Family Trust", entity_type="trust", jurisdiction="NZ", governance={"trustees": ["Mary Ng", "Lawyer"]}))
            m4, a4 = await mandate_of(nz, mary, "Mary Sole", cash=500)
            m5, a5 = await mandate_of(nz, sam, "Sam Sole")
            m6, a6 = await mandate_of(nz, mary, "Mary NoBen", cash=100)
            ccc = await instrument(nz, "CCC", [(ago(10), 7), (today, 11)])
            h4 = await holding(nz, a4, ccc, (100, 2, ago(500)), price=11)
            await holding(nz, a6, ccc, (10, 2, ago(500)), price=11)
            s.add_all([AccountBeneficiary(firm_id=nz.id, account_id=a4.id, beneficiary_name="Sam Ng", percentage=60, designation_class="primary"),
                       AccountBeneficiary(firm_id=nz.id, account_id=a4.id, beneficiary_name="Tui Ng", percentage=40, designation_class="primary")])
            sold_after = ago(2)
            h4.quantity = 90
            trade = Transaction(firm_id=nz.id, account_id=a4.id, instrument_id=ccc.id, txn_type="sell", quantity=10, price=11, amount=110, trade_date=sold_after, lineage={"fees": 0})
            s.add(trade)
            a4.cash_balance = 610
            await s.flush()
            death = {"person_id": str(mary.id), "date_of_death": ago(5).isoformat(), "source_document": "Death certificate 1188"}
            shown = await card(ex, "record_client_death", death)
            check("the card names the person, day and document", (shown["Person"], shown["Document"], shown["Executor"]), ("Mary Ng", "Death certificate 1188", "None on file"))
            check("it values the book that day, at that day's price", "1,370" in shown["Value then"], True)
            await refused("a date to come", ex.preview_change("record_client_death", {**death, "date_of_death": (today + timedelta(days=1)).isoformat()}), "future")
            await refused("no date", ex.preview_change("record_client_death", {**death, "date_of_death": None}), "date of death")
            await refused("no document", ex.preview_change("record_client_death", {**death, "source_document": "x"}), "which document")
            await refused("another firm's person", outsider.preview_change("record_client_death", death), "not found")
            check("showing it records nothing", (mary.deceased_on, m4.exit_hold), (None, None))
            estate = await ex.record_client_death(death)
            eid = estate["id"]
            check("the person is recorded, the mandates held", (mary.deceased_on, m4.exit_hold, m5.exit_hold), (ago(5), "deceased", None))
            check("the book kept is the book that day", estate["accounts"][0]["value_then"] if estate["accounts"][0]["name"] == "Mary NoBen A/C" else estate["accounts"][1]["value_then"], 170.0)
            check("nothing is missing from it", estate["book_complete"], True)
            check("the checks name what is open", (estate["checks"]["no_executor"], estate["checks"]["trustee_of"], estate["checks"]["through_estate"]),
                  (True, ["Ng Family Trust"], ["Mary NoBen A/C"]))
            check("nothing trades with no executor", "no executor is on file" in exits.order_refusal(m4, [{"side": "sell"}]), True)
            await refused("a death is recorded once", ex.preview_change("record_client_death", death), "already on record")
            check("protection stops counting them", [p["name"] for p in (await protection.for_household(s, ng.id, firm_id=nz.id))["people"]], ["Sam Ng"])
            check("the brain says so", next(p["deceased_on"] for p in (await household_brain(s, ng.id, firm_id=nz.id))["persons"] if p["full_name"] == "Mary Ng"), ago(5).isoformat())

            print("\n=== Paying the beneficiaries ===")
            pay = {"exit_id": eid, "account_id": str(a4.id), "transfer_date": today.isoformat(), "source_document": "Custodian payment advice 22"}
            await refused("someone not on the designation", run.preview_change("record_account_transfer", {**pay, "to_party": "A Stranger"}), "not a beneficiary", "Sam Ng", "Tui Ng")
            await refused("an account with none, and no executor", run.preview_change("record_account_transfer", {**pay, "account_id": str(a6.id), "to_party": "The estate"}), "passes through the estate", "no executor")
            shown = await card(run, "record_account_transfer", {**pay, "to_party": "Sam Ng"})
            check("the share is the designation's", (shown["Footing"], shown["Share"], "Account then" in shown), ("Beneficiary designation", "60.0%", False))
            shown = await card(ToolExecutors(s, ops.id, ops.role, nz.id), "record_account_transfer", {**pay, "to_party": "sam ng", "share_pct": 100})
            check("a share can be stated", shown["Share"], "100.0%")
            await run.record_account_transfer({**pay, "to_party": "Sam Ng"})
            check("60% of what stood at death leaves", (float(h4.quantity), float(a4.cash_balance)), (30.0, 310.0))
            await refused("more than is left", run.preview_change("record_account_transfer", {**pay, "to_party": "Tui Ng", "share_pct": 50}), "too much")
            await run.record_account_transfer({**pay, "to_party": "Tui Ng"})
            check("40% more leaves nothing", (float(h4.quantity), float(a4.cash_balance), a4.status), (0.0, 0.0, "closed"))

            print("\n=== An executor ===")
            await refused("an executor needs a name", ex.preview_change("record_executor", {"exit_id": eid, "executor": "", "source_document": "Grant of probate"}), "executor's name")
            shown = await card(ex, "record_executor", {"exit_id": eid, "executor": "Lawyer", "source_document": "Grant of probate"})
            check("the card names them", (shown["Estate of"], shown["Executor"]), ("Mary Ng", "Lawyer"))
            await ex.record_executor({"exit_id": eid, "executor": "Lawyer", "source_document": "Grant of probate"})
            check("sales may go ahead, buys not", (m6.exit_hold, exits.order_refusal(m6, [{"side": "sell"}]), "in an estate" in exits.order_refusal(m6, [{"side": "buy"}])), ("executor", None, True))
            shown = await card(run, "record_account_transfer", {**pay, "account_id": str(a6.id), "to_party": "Estate of Mary Ng"})
            check("with no beneficiary it goes through the estate", shown["Footing"], "Through the estate")
            await run.record_account_transfer({**pay, "account_id": str(a6.id), "to_party": "Estate of Mary Ng"})
            await run.close_client_exit({"exit_id": eid})
            check("an estate of one spouse leaves the household", (ng.left_on, m5.is_active), (None, True))

            print("\n=== Basis, where the law says ===")
            usday = firm_time.today(us)
            cho = await household_of(us, "Cho")
            ed, joy = await person_of(us, cho, "Ed Cho"), await person_of(us, cho, "Joy Cho")
            um, ua = await mandate_of(us, ed, "Ed Taxable", cash=0)
            ui, ia = await mandate_of(us, ed, "Ed IRA", cash=0, registration="traditional_ira")
            uj, ja = await mandate_of(us, joy, "Joy Taxable", cash=0)
            uk, ka = await mandate_of(us, joy, "Joy Inherited", cash=0)
            ka.registration_type = "inherited_ira"
            usa = await instrument(us, "USA", [(usday - timedelta(days=3), 50), (usday, 60)])
            await holding(us, ua, usa, (10, 5, usday - timedelta(days=800)), price=60)
            await holding(us, ia, usa, (10, 5, usday - timedelta(days=800)), price=60)
            s.add_all([AccountBeneficiary(firm_id=us.id, account_id=ua.id, beneficiary_name="Joy Cho", percentage=100, designation_class="primary"),
                       AccountBeneficiary(firm_id=us.id, account_id=ia.id, beneficiary_name="Joy Cho", percentage=100, designation_class="primary")])
            await s.flush()
            usx = ToolExecutors(s, ops.id, UserRole.OPERATIONS, us.id)
            ue = await usx.record_client_death({"person_id": str(ed.id), "date_of_death": (usday - timedelta(days=3)).isoformat(), "source_document": "Death certificate 9"})
            to_joy = {"exit_id": ue["id"], "transfer_date": usday.isoformat(), "source_document": "Custodian journal 3", "to_party": "Joy Cho"}
            shown = await card(usx, "record_account_transfer", {**to_joy, "account_id": str(ua.id), "to_account_id": str(ja.id)})
            check("a US heir's taxable basis steps up", shown["Basis"], "Steps up to the value at death")
            await usx.record_account_transfer({**to_joy, "account_id": str(ua.id), "to_account_id": str(ja.id)})
            arrived = (await s.execute(select(Holding).where(Holding.account_id == ja.id))).scalar_one()
            lot = (await lots_of(arrived))[0]
            check("the units arrive at the value at death", (float(arrived.quantity), float(lot.cost_per_unit), lot.acquired_on), (10.0, 50.0, usday - timedelta(days=3)))
            await refused("a retirement account needs its wrapper", usx.preview_change("record_account_transfer", {**to_joy, "account_id": str(ia.id), "to_account_id": str(ja.id)}), "inherited_ira")
            shown = await card(usx, "record_account_transfer", {**to_joy, "account_id": str(ia.id), "to_account_id": str(ka.id)})
            check("a retirement account keeps its basis", shown["Basis"], "Carried over")
            await usx.record_account_transfer({**to_joy, "account_id": str(ia.id), "to_account_id": str(ka.id)})
            kept = (await s.execute(select(Holding).where(Holding.account_id == ka.id))).scalar_one()
            check("the original lot comes across", float((await lots_of(kept))[0].cost_per_unit), 5.0)
            check("and the inherited clock starts", ka.original_owner_death_date, usday - timedelta(days=3))
            view = await exits.view_of(s, uuid.UUID(ue["id"]), firm_id=us.id)
            check("what stayed at the firm is counted", (view["stayed_at_firm"], view["left_firm"]), (1200.0, 0.0))

            for firm, heirs, label in ((nz, "NZD", "New Zealand"), (india, "INR", "India")):
                h = await household_of(firm, f"Heir {heirs}")
                dead, heir = await person_of(firm, h, "Dead One"), await person_of(firm, h, "Heir One")
                dm, da = await mandate_of(firm, dead, f"Dead {heirs}", cash=0)
                hm, ha = await mandate_of(firm, heir, f"Heir {heirs}", cash=0)
                day = firm_time.today(firm)
                inst = await instrument(firm, f"X{heirs}", [(day - timedelta(days=2), 40)])
                await holding(firm, da, inst, (5, 3, day - timedelta(days=900)), price=40)
                s.add(AccountBeneficiary(firm_id=firm.id, account_id=da.id, beneficiary_name="Heir One", percentage=100, designation_class="primary"))
                await s.flush()
                fx = ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id)
                e = await fx.record_client_death({"person_id": str(dead.id), "date_of_death": (day - timedelta(days=1)).isoformat(), "source_document": "Certificate 5"})
                shown = await card(fx, "record_account_transfer", {"exit_id": e["id"], "account_id": str(da.id), "transfer_date": day.isoformat(),
                                                                   "source_document": "Journal 5", "to_party": "Heir One", "to_account_id": str(ha.id)})
                check(f"{label} carries the basis", shown["Basis"], "Carried over")

            print("\n=== A record made in error ===")
            pat = await household_of(nz, "Pat")
            wrong = await person_of(nz, pat, "Pat Ora")
            pm, _ = await mandate_of(nz, wrong, "Pat One")
            e = await ex.record_client_death({"person_id": str(wrong.id), "date_of_death": ago(1).isoformat(), "source_document": "Email from a relative"})
            shown = await card(ex, "withdraw_client_exit", {"exit_id": e["id"], "reason": "Wrong person"})
            check("the card says the date of death clears", shown["Date of death cleared"], "Pat Ora")
            await ex.withdraw_client_exit({"exit_id": e["id"], "reason": "Wrong person"})
            check("the person and mandate are clear again", (wrong.deceased_on, pm.exit_hold), (None, None))

            print("\n=== Reading it ===")
            read = await ex.read_client_exits({})
            check("the firm's list carries a sentence", "estate(s) open" in read["message"], True)
            check("one household's", len((await ex.read_client_exits({"household_id": str(kim.id)}))["exits"]), 1)
            await refused("another firm's household", outsider.read_client_exits({"household_id": str(kim.id)}), "not found")
            check("another firm sees none of it", (await outsider.read_client_exits({}))["exits"], [])
            check("what is not modelled is said", "estate_tax" in read["exits"][0]["not_modelled"], True)
            check("money stayed Decimal-safe", isinstance(Decimal(str(read["summary"]["left_firm"])), Decimal), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
