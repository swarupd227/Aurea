"""Prove the shelf decides what can be bought: closed stops every purchase, a gate needs a certification from a
document, approved-only is a switch a person throws, and the rebalancer and a direct order read the same answer.
Sales are never stopped.

Runs against a throwaway SQLite database.

    python -m tests.verify_shelf
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.drift_rebalancing import DriftRebalancingAgent
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, shelf
from app.core.db import Base, utcnow
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AutonomyTier, MarketType, UserRole
from app.models.governance import AgentRun
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot
from app.models.shelf import InvestorCertification, ProductDecision
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, shelf.ShelfError) as e:
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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            cio = User(id=uuid.uuid4(), firm_id=firm.id, email="c@t", full_name="C", role=UserRole.RESEARCH_CIO, hashed_password="x")
            s.add_all([adviser, cio])
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Ann")
            s.add(h)
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee")
            s.add(ann)
            await s.flush()
            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Growth", drift_band=0.05)
            s.add(model)
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory",
                              suitability={"risk_profile": "growth", "capacity_for_loss": "high"}, constraints={}, model_portfolio_id=model.id)
            s.add(mandate)
            await s.flush()
            account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Ann A/C", currency="NZD", cash_balance=50000)
            s.add(account)
            await s.flush()

            async def instrument(symbol, cls="equity", price=10.0, market=MarketType.PUBLIC):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class=cls, market_type=market, currency="NZD")
                s.add(i)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=price, currency="NZD", source="test", is_real=True))
                await s.flush()
                return i

            aaa, bbb, ccc, pvt = await instrument("AAA"), await instrument("BBB"), await instrument("CCC"), await instrument("PVT", "alternatives", market=MarketType.PRIVATE)
            fund_i, fund_a = await instrument("FUNDI"), await instrument("FUNDA")
            held = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=aaa.id, quantity=100, market_value=1000, cost_basis=800)
            dear = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=fund_a.id, quantity=100, market_value=1000, cost_basis=900)
            s.add_all([held, dear])
            await s.flush()
            s.add_all([TaxLot(firm_id=firm.id, holding_id=held.id, quantity=100, cost_per_unit=8, acquired_on=today - timedelta(days=300)),
                       TaxLot(firm_id=firm.id, holding_id=dear.id, quantity=100, cost_per_unit=9, acquired_on=today - timedelta(days=300))])
            await s.flush()

            ex, research = ToolExecutors(s, adviser.id, adviser.role, firm.id), ToolExecutors(s, cio.id, cio.role, firm.id)
            outsider = ToolExecutors(s, cio.id, cio.role, other.id)

            async def card(executor, tool, inputs):
                return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

            def buy(*symbols):
                return {"mandate_id": str(mandate.id), "orders": [{"symbol": x, "side": "buy", "quantity": 1} for x in symbols]}

            def sell(symbol):
                return {"mandate_id": str(mandate.id), "orders": [{"symbol": symbol, "side": "sell", "quantity": 1}]}

            print("\n=== Nothing decided ===")
            read = await ex.read_product_shelf({})
            check("nothing is stopped", (await shelf.barred(s, firm, mandate), "Order 1" in await card(ex, "execute_orders", buy("BBB"))), ({}, True))
            check("what is held reads as no decision", read["summary"]["not_reviewed"], 2)
            check("it says so", "with no decision here" in read["message"], True)

            print("\n=== A decision ===")
            close = {"symbol": "AAA", "status": "closed", "rationale": "Manager left the strategy", "document": "Research note 41"}
            shown = await card(research, "record_product_decision", close)
            check("the card names the product, effect and what is held", (shown["Product"], shown["Effect"], "1,000" in shown["Held"]),
                  ("AAA · AAA", "No new purchases, for anyone", True))
            await refused("an unknown status", research.preview_change("record_product_decision", {**close, "status": "maybe"}), "status must be")
            await refused("no reason", research.preview_change("record_product_decision", {**close, "rationale": ""}), "reason")
            await refused("no paper", research.preview_change("record_product_decision", {**close, "document": "x"}), "due-diligence paper")
            await refused("no such product", research.preview_change("record_product_decision", {**close, "symbol": "ZZZ"}), "no instrument")
            await refused("a next review in the past", research.preview_change("record_product_decision", {**close, "next_review_on": (today - timedelta(days=1)).isoformat()}), "must come after")
            await refused("an expense that is no number", research.preview_change("record_product_decision", {**close, "expense_ratio_pct": "cheap"}), "must be a number")
            await refused("another firm's product", outsider.preview_change("record_product_decision", close), "no instrument")
            check("showing it writes nothing", (await s.execute(select(func.count()).select_from(ProductDecision))).scalar(), 0)
            await research.record_product_decision(close)
            await refused("closed stops a purchase", ex.preview_change("execute_orders", buy("AAA")), "closed to new money")
            check("a sale goes ahead", "Sell 1 AAA" in (await card(ex, "execute_orders", sell("AAA")))["Order 1"], True)
            check("a product with no decision is still bought", "Order 1" in await card(ex, "execute_orders", buy("BBB")), True)
            check("and the read shows it is held while closed", (await ex.read_product_shelf({}))["summary"]["held_while_closed"], 1)
            await research.record_product_decision({**close, "status": "approved", "rationale": "New manager reviewed", "document": "Research note 42"})
            shown = await card(ex, "execute_orders", buy("AAA"))
            check("the latest decision is the one in force", "Order 1" in shown, True)
            shown = await card(research, "record_product_decision", {**close, "status": "watch", "document": "Research note 43", "rationale": "Performance slipping"})
            check("a change shows from and to", shown["Status"], "Approved becomes Watch")
            await research.record_product_decision({**close, "status": "watch", "document": "Research note 43", "rationale": "Performance slipping"})
            check("under watch a purchase goes ahead, and is named", (await card(ex, "execute_orders", buy("AAA")))["Under watch"], "AAA")

            print("\n=== Who may buy ===")
            gate = {"symbol": "PVT", "status": "approved", "rationale": "Reviewed private fund", "document": "Research note 50", "minimum_class": "accredited"}
            shown = await card(research, "record_product_decision", gate)
            check("the gate is on the card", shown["Investors"], "Accredited only")
            await refused("an unknown class", research.preview_change("record_product_decision", {**gate, "minimum_class": "rich"}), "minimum class")
            await research.record_product_decision(gate)
            await refused("no certification on file", ex.preview_change("execute_orders", buy("PVT")), "accredited investor", "no such certification")
            cert = {"person_id": str(ann.id), "investor_class": "accredited", "document": "Accountant's letter, Oct 2026", "expires_on": (today + timedelta(days=30)).isoformat()}
            shown = await card(ex, "record_investor_status", cert)
            check("the card names the person and class", (shown["Person"], shown["Class"], shown["Replaces"]), ("Ann Lee", "Accredited", "Nothing on file"))
            await refused("an unknown class", ex.preview_change("record_investor_status", {**cert, "investor_class": "wealthy"}), "class must be")
            await refused("no document", ex.preview_change("record_investor_status", {**cert, "document": ""}), "letter or form")
            await refused("run out before it starts", ex.preview_change("record_investor_status", {**cert, "expires_on": (today - timedelta(days=1)).isoformat()}), "run out")
            await refused("another firm's person", outsider.preview_change("record_investor_status", cert), "not found")
            check("showing it records nothing", (await s.execute(select(func.count()).select_from(InvestorCertification))).scalar(), 0)
            await ex.record_investor_status(cert)
            check("certified, it may be bought", "Order 1" in await card(ex, "execute_orders", buy("PVT")), True)
            await research.record_product_decision({**gate, "minimum_class": "qualified_purchaser", "document": "Research note 51"})
            await refused("a higher gate still stops it", ex.preview_change("execute_orders", buy("PVT")), "qualified purchaser")
            await ex.record_investor_status({**cert, "investor_class": "qualified_purchaser", "as_of": (today - timedelta(days=40)).isoformat(), "expires_on": (today - timedelta(days=1)).isoformat()})
            await refused("one that has run out does not count, and the latest by date stands", ex.preview_change("execute_orders", buy("PVT")), "qualified purchaser")

            print("\n=== Approved products only ===")
            await refused("off is off already", research.preview_change("set_approved_products_only", {"on": "false"}), "already off")
            shown = await card(research, "set_approved_products_only", {"on": True})
            check("the card names what would stop", (shown["Approved products only"], shown["Held with no decision"], "1,000" in shown["Their value"]), ("Off becomes on", "FUNDA", True))
            check("showing it changes nothing", shelf.enforced(firm), False)
            await research.set_approved_products_only({"on": True})
            await refused("a product with no decision stops", ex.preview_change("execute_orders", buy("BBB")), "not on the approved list")
            check("a holding with none may still be sold", "Sell 1 FUNDA" in (await card(ex, "execute_orders", sell("FUNDA")))["Order 1"], True)

            print("\n=== The rebalancer reads the same answer ===")
            s.add(TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=1.0, instrument_id=bbb.id))
            await s.flush()
            run = AgentRun(firm_id=firm.id, agent_key="drift_rebalancing", status="sensing", tier="tier_2", trigger="manual", started_at=utcnow())
            s.add(run)
            await s.flush()
            ctx = AgentContext(session=s, firm=firm, run=run, tier=AutonomyTier.TIER_2, guardrails={}, config={}, subject=Subject("mandate", mandate.id, mandate.name))
            sensed = await DriftRebalancingAgent().sense(ctx)
            check("a model's pick off the shelf is not bought", [p["symbol"] for p in sensed.get("model_picks", [])], [])
            await research.record_product_decision({"symbol": "BBB", "status": "approved", "rationale": "Reviewed core holding", "document": "Research note 60"})
            sensed = await DriftRebalancingAgent().sense(ctx)
            check("once approved it is", [p["symbol"] for p in sensed.get("model_picks", [])], ["BBB"])

            print("\n=== Share classes ===")
            await research.record_product_decision({"symbol": "FUNDI", "status": "approved", "rationale": "Institutional class", "document": "Research note 70", "family": "Global Fund", "share_class": "I", "expense_ratio_pct": 0.4})
            await research.record_product_decision({"symbol": "FUNDA", "status": "approved", "rationale": "Retail class", "document": "Research note 71", "family": "Global Fund", "share_class": "A", "expense_ratio_pct": 1.1,
                                                    "next_review_on": (today + timedelta(days=1)).isoformat()})
            result = await ex.read_product_shelf({})
            row = next(p for p in result["products"] if p["symbol"] == "FUNDA")
            check("a dearer class of one fund is found", (row["dearer_class"], row["expense_ratio"], result["summary"]["dearer_class"]), (True, 0.011, 1))
            check("and the cheapest is not", next(p for p in result["products"] if p["symbol"] == "FUNDI")["dearer_class"], False)
            row_id = (await s.execute(select(ProductDecision).where(ProductDecision.instrument_id == fund_a.id))).scalar_one()
            row_id.next_review_on = today - timedelta(days=1)
            await s.flush()
            check("a review past its date is counted", (await ex.read_product_shelf({}))["summary"]["reviews_overdue"], 1)
            check("another firm sees none of it", (await outsider.read_product_shelf({}))["products"], [])
            check("nor is it bound by it", shelf.enforced(other), False)
            await research.set_approved_products_only({"on": False})
            check("it can be turned off", shelf.enforced(firm), False)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
