"""Prove the rebalancer will not propose a wash sale, rather than propose one and report it.

The guard reads what the household did in the window - every account, a spouse's included - and the
solver works around it: it does not sell at a loss what was just bought, and does not buy back what
was just sold at a loss. It binds a household taxed in the US and nobody else. Approval re-checks it,
because a proposal can wait days while another account trades.

Runs against a throwaway SQLite database; the model and the research store are stubbed.

    python -m tests.verify_wash_sale_guard
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import drift_rebalancing as drift
from app.atlas.base import AgentContext, Subject
from app.aurea_core import wash_sale as ws
from app.aurea_core.rebalancing import Lot, Position, WashSaleGuard, optimise
from app.compliance.rules import CheckContext, wash_sale as wash_rule
from app.core.db import Base
from app.models.enums import AutonomyTier, MarketType
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []
WINDOW = {"on": "2026-10-18", "account": "Spouse A/C", "clear_on": "2026-11-18"}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pos(sym, cls, lots, price=100.0, account="a1", **kw) -> Position:
    qty = sum(q for q, _ in lots)
    return Position(holding_id=sym, instrument_id=sym, symbol=sym, name=sym, asset_class=cls,
                    market_value=qty * price, price=price, cost_basis=sum(q * c for q, c in lots),
                    account_id=account, custodian="X", lots=[Lot(q, c) for q, c in lots], **kw)


def solver() -> None:
    target = {"equity": 0.5, "fixed_income": 0.5}
    book = [pos("AAA", "equity", [(100, 130.0), (100, 80.0)]), pos("BND", "fixed_income", [(40, 100.0)])]

    print("\n=== The solver with nothing to avoid ===")
    plain = optimise(positions=book, target_weights=target, cash=0.0)
    sells = [o for o in plain.orders if o.side == "sell"]
    check("it harvests the loss lot first", (sells[0].symbol, sells[0].est_realised_gain < 0), ("AAA", True))
    check("and nothing is held", plain.wash_sale_held, [])
    same = optimise(positions=book, target_weights=target, cash=0.0, wash=WashSaleGuard())
    check("an empty guard changes nothing", [(o.side, o.symbol, o.quantity) for o in same.orders],
          [(o.side, o.symbol, o.quantity) for o in plain.orders])

    print("\n=== Bought in the window: no sale at a loss ===")
    held = optimise(positions=book, target_weights=target, cash=0.0, wash=WashSaleGuard(no_loss_sale={"AAA": WINDOW}))
    sells = [o for o in held.orders if o.side == "sell"]
    check("the sale takes the gain lot instead", (len(sells), sells[0].est_realised_gain > 0), (1, True))
    check("no loss is harvested", held.harvested_losses, 0)
    h = held.wash_sale_held[0]
    check("what was held back is recorded as figures",
          (h["side"], h["symbol"], h["on"], h["account"], h["clear_on"], h["loss"] > 0),
          ("sell", "AAA", "2026-10-18", "Spouse A/C", "2026-11-18", True))
    check("and said in the limitations", any("held back" in x and "2026-11-18" in x for x in held.limitations), True)

    only_loss = [pos("AAA", "equity", [(200, 130.0)]), pos("CCC", "equity", [(100, 80.0)]),
                 pos("BND", "fixed_income", [(40, 100.0)])]
    moved = optimise(positions=only_loss, target_weights=target, cash=0.0,
                     wash=WashSaleGuard(no_loss_sale={"AAA": WINDOW}))
    check("a position with only loss lots is left, and the next one takes the sale",
          sorted(o.symbol for o in moved.orders if o.side == "sell"), ["CCC"])

    stuck = optimise(positions=[pos("AAA", "equity", [(200, 130.0)]), pos("BND", "fixed_income", [(40, 100.0)])],
                     target_weights=target, cash=0.0, wash=WashSaleGuard(no_loss_sale={"AAA": WINDOW}))
    check("with nothing else to sell, nothing is sold", [o for o in stuck.orders if o.side == "sell"], [])
    check("and it says what it held", stuck.wash_sale_held[0]["symbol"], "AAA")

    gain_only = optimise(positions=[pos("AAA", "equity", [(200, 80.0)]), pos("BND", "fixed_income", [(40, 100.0)])],
                         target_weights=target, cash=0.0, wash=WashSaleGuard(no_loss_sale={"AAA": WINDOW}))
    check("a gain lot is no wash sale, however recent the purchase",
          (len([o for o in gain_only.orders if o.side == "sell"]), gain_only.wash_sale_held), (1, []))

    print("\n=== Sold at a loss in the window: no buying it back ===")
    over = pos("AAA", "equity", [(200, 80.0)])
    two = optimise(positions=[over, pos("BND", "fixed_income", [(40, 100.0)]), pos("BN2", "fixed_income", [(10, 100.0)])],
                   target_weights=target, cash=0.0, wash=WashSaleGuard(no_buy={"BND": WINDOW}))
    check("the next holding in the class is bought instead",
          [o.symbol for o in two.orders if o.side == "buy"], ["BN2"])
    picks = {"fixed_income": pos("MDL", "fixed_income", [(0, 0.0)], account="")}
    model = optimise(positions=[over, pos("BND", "fixed_income", [(40, 100.0)])], target_weights=target, cash=0.0,
                     model_instruments=picks, wash=WashSaleGuard(no_buy={"BND": WINDOW}))
    buys = [o for o in model.orders if o.side == "buy"]
    check("or the model's instrument", [o.symbol for o in buys], ["MDL"])
    check("in an account that exists", buys[0].account_id, "a1")
    none = optimise(positions=[over, pos("BND", "fixed_income", [(40, 100.0)])], target_weights=target, cash=0.0,
                    wash=WashSaleGuard(no_buy={"BND": WINDOW}))
    check("with no alternative, nothing is bought", [o for o in none.orders if o.side == "buy"], [])
    check("and the held buy says when it clears", (none.wash_sale_held[0]["side"], none.wash_sale_held[0]["clear_on"]),
          ("buy", "2026-11-18"))

    print("\n=== Two faults on the way through ===")
    fresh = optimise(positions=[over], target_weights=target, cash=0.0,
                     model_instruments={"fixed_income": pos("MDL", "fixed_income", [(0, 0.0)], account="")})
    check("a class nobody holds is bought into a real account, not an empty one",
          [o.account_id for o in fresh.orders if o.side == "buy"], ["a1"])
    empty = optimise(positions=[], target_weights=target, cash=0.0)
    check("an empty book no longer raises", (empty.needs_rebalance, empty.orders), (False, []))


def rule() -> None:
    print("\n=== The compliance rule ===")
    ctx = lambda p: CheckContext(**{"payload": p, **_ctx_defaults()})
    sell = {"side": "sell", "symbol": "AAA", "est_realised_gain": -10}
    check("a loss sale and a buy in one set still fails",
          wash_rule(ctx({"order_set": [sell, {"side": "buy", "symbol": "AAA"}]})).status, "fail")
    guarded = wash_rule(ctx({"order_set": [sell], "wash_sale": {"applies": True}}))
    check("a guarded set says it checked the household", (guarded.status, "every account" in guarded.finding), ("pass", True))
    plain = wash_rule(ctx({"order_set": [sell]}))
    check("an unguarded one claims only the order set", (plain.status, "this order set" in plain.finding), ("pass", True))


def _ctx_defaults() -> dict:
    import dataclasses
    return {f.name: None for f in dataclasses.fields(CheckContext)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING and f.name != "payload"}


async def main() -> int:
    solver()
    rule()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date.today()

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="NZ")
            us_firm = Firm(id=uuid.uuid4(), name="U", slug="u", base_currency="USD", jurisdiction="US")
            s.add_all([firm, us_firm])
            await s.flush()

            def inst(sym, cls):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class=cls,
                               market_type=MarketType.PUBLIC, currency="USD")
                s.add(i)
                return i

            aaa, bnd, oth = inst("AAA", "equity"), inst("BND", "fixed_income"), inst("OTH", "equity")
            await s.flush()
            for i in (aaa, bnd, oth):
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=100.0, currency="USD",
                            source="yahoo", is_real=True))

            async def household(name, jurisdiction=None, f=firm):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name,
                              values={"jurisdiction": jurisdiction} if jurisdiction else {})
                s.add(h)
                await s.flush()
                return h

            async def member(h, name, f=firm, model=None):
                p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=name)
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=f.id, person_id=p.id, name=f"{name} mandate",
                            mandate_type="discretionary", model_portfolio_id=model.id if model else None)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=f.id, mandate_id=m.id, name=f"{name} A/C", currency="USD",
                            cash_balance=0)
                s.add(a)
                await s.flush()
                return m, a

            async def holding(a, i, lots):
                h = Holding(id=uuid.uuid4(), firm_id=a.firm_id, account_id=a.id, instrument_id=i.id,
                            quantity=sum(q for q, _, _ in lots), market_value=sum(q for q, _, _ in lots) * 100.0,
                            cost_basis=sum(q * c for q, c, _ in lots))
                s.add(h)
                await s.flush()
                for q, c, days in lots:
                    s.add(TaxLot(firm_id=a.firm_id, holding_id=h.id, quantity=q, cost_per_unit=c,
                                 acquired_on=today - timedelta(days=days)))
                await s.flush()
                return h

            def txn(a, i, kind, days, gain=None):
                s.add(Transaction(firm_id=a.firm_id, account_id=a.id, instrument_id=i.id, txn_type=kind, quantity=10,
                                  price=100, amount=1000, trade_date=today - timedelta(days=days),
                                  lineage={} if gain is None else {"realised_gain": gain}))

            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="50/50", drift_band=0.05)
            s.add(model)
            await s.flush()
            s.add_all([TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=0.5),
                       TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="fixed_income", target_weight=0.5)])

            print("\n=== Reading the household's window ===")
            us = await household("Morrison", "US")
            m1, a1 = await member(us, "Robert", model=model)
            m2, a2 = await member(us, "Helen")
            await holding(a2, aaa, [(10, 100.0, 12)])
            await s.flush()
            g = await ws.guard(s, firm, m1, today=today)
            check("a household taxed in the US is bound", (g["applies"], g["jurisdiction"]), (True, "US"))
            row = g["no_loss_sale"].get(str(aaa.id)) or {}
            check("a purchase in the spouse's account counts", row.get("account"), "Helen A/C")
            check("and clears the day after the window",
                  row.get("clear_on"), (today - timedelta(days=12) + timedelta(days=31)).isoformat())

            await holding(a2, bnd, [(10, 100.0, 31)])
            await holding(a2, oth, [(10, 100.0, 30)])
            g = await ws.guard(s, firm, m1, today=today)
            check("31 days ago is outside the window", str(bnd.id) in g["no_loss_sale"], False)
            check("30 days ago is inside it", str(oth.id) in g["no_loss_sale"], True)

            txn(a1, bnd, "buy", 5)
            txn(a1, oth, "sell", 3, gain=-50.0)
            await s.flush()
            g = await ws.guard(s, firm, m1, today=today)
            check("a purchase since sold still counts", str(bnd.id) in g["no_loss_sale"], True)
            check("a sale at a loss bars buying back", g["no_buy"][str(oth.id)]["account"], "Robert A/C")
            txn(a1, aaa, "sell", 3, gain=75.0)
            await s.flush()
            g = await ws.guard(s, firm, m1, today=today)
            check("a sale at a gain does not", str(aaa.id) in g["no_buy"], False)

            nz = await household("Chen")
            m3, a3 = await member(nz, "Wei", model=model)
            await holding(a3, bnd, [(10, 100.0, 2)])
            g = await ws.guard(s, firm, m3, today=today)
            check("an NZ household is not bound, and nothing is read for it",
                  (g["applies"], g["no_loss_sale"], g["no_buy"]), (False, {}, {}))
            g = await ws.guard(s, firm, m1, today=today)
            check("another household's trades are not this one's", g["no_loss_sale"][str(bnd.id)]["account"], "Robert A/C")

            amer = await household("On a US firm", f=us_firm)
            m4, _ = await member(amer, "Dana", f=us_firm)
            check("a household with no stated jurisdiction takes the firm's",
                  (await ws.guard(s, us_firm, m4, today=today))["applies"], True)
            uk = await household("UK on a US firm", "UK", f=us_firm)
            m5, _ = await member(uk, "Ian", f=us_firm)
            check("a household's own jurisdiction wins over the firm's",
                  (await ws.guard(s, us_firm, m5, today=today))["applies"], False)

            print("\n=== Checking an order set again at approval ===")
            rules = {"applies": True, "no_loss_sale": {"X": WINDOW}, "no_buy": {"Y": WINDOW}}
            order = lambda side, iid, gain=0: {"side": side, "instrument_id": iid, "symbol": iid, "est_realised_gain": gain}
            check("a sale at a loss of something just bought", [c["symbol"] for c in ws.conflicts([order("sell", "X", -5)], rules)], ["X"])
            check("a sale at a gain of it is fine", ws.conflicts([order("sell", "X", 5)], rules), [])
            check("a buy of something just sold at a loss", [c["side"] for c in ws.conflicts([order("buy", "Y")], rules)], ["buy"])
            check("nothing when the rule does not apply", ws.conflicts([order("sell", "X", -5)], {**rules, "applies": False}), [])

            print("\n=== The agent, end to end ===")
            async def _no_research(*a, **k):
                return []

            async def _fallback(ctx, *, fallback, **k):
                return fallback

            drift.knowledge.retrieve = _no_research
            drift.narrate = _fallback

            def ctx_for(m):
                return AgentContext(session=s, firm=firm, run=None, tier=AutonomyTier.TIER_2, guardrails={}, config={},
                                    subject=Subject("mandate", m.id, m.name))

            async def propose(m):
                agent = drift.DriftRebalancingAgent()
                c = ctx_for(m)
                sensed = await agent.sense(c)
                drafts = await agent.think(c, sensed)
                return drafts[0]

            # Robert holds 200 AAA, half of it at a loss, and 40 BND. Helen bought AAA 12 days ago.
            await holding(a1, aaa, [(100, 130.0, 400), (100, 80.0, 400)])
            await holding(a1, bnd, [(40, 100.0, 400)])
            d = await propose(m1)
            sell = [o for o in d.payload["order_set"] if o["side"] == "sell"][0]
            check("the US household's proposal sells the gain lot, not the loss",
                  (sell["symbol"], sell["est_realised_gain"] > 0), ("AAA", True))
            check("nothing harvested", d.payload["harvested_losses"], 0)
            check("the proposal says what it held and why",
                  [(h["symbol"], h["account"]) for h in d.payload["wash_sale"]["held"]], [("AAA", "Helen A/C")])
            check("and the guard is on the record", (d.payload["wash_sale"]["applies"],
                                                     d.payload["wash_sale"]["jurisdiction"]), (True, "US"))
            check("the adviser reads it in the limitations",
                  any("held back" in x for x in d.evidence["limitations"]), True)

            await holding(a3, aaa, [(100, 130.0, 400), (100, 80.0, 400)])
            n = await propose(m3)
            nz_sell = [o for o in n.payload["order_set"] if o["side"] == "sell"][0]
            check("the NZ household still harvests the same loss",
                  (nz_sell["est_realised_gain"] < 0, n.payload["wash_sale"]["held"]), (True, []))

            print("\n=== A purchase between proposal and approval ===")
            agent = drift.DriftRebalancingAgent()
            stale = {"order_set": [{"side": "sell", "instrument_id": str(aaa.id), "symbol": "AAA", "quantity": 10,
                                    "est_price": 100.0, "est_value": 1000.0, "account_id": str(a1.id),
                                    "custodian": "X", "est_realised_gain": -300.0, "reason": "Harvest"}]}
            rec = SimpleNamespace(id=uuid.uuid4(), payload=stale, modified_payload=None)
            out = await agent.act(ctx_for(m1), rec)
            check("nothing is placed", (out["executed"], (await s.execute(select(func.count()).select_from(Order))).scalar()),
                  (False, 0))
            check("and it says which and what to do", ("AAA" in out["note"] and "Run the agent again" in out["note"],
                                                       out["wash_sale_held"][0]["account"]), (True, "Helen A/C"))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
