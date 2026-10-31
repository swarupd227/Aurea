"""Prove the Indian tax position is worked taxpayer by taxpayer, first-in first-out, and that the
rebalancer treats India as India.

No lot choice, no netting across a family, the exemption and set-off as the Act has them, and a guard
that is not a port of the US wash-sale rule. Dates near the 12-month line are checked by the day,
because that is where the money is.

Runs against a throwaway SQLite database; the model and the research store are stubbed.

    python -m tests.verify_india_tax
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import drift_rebalancing as drift
from app.agents import tax_intelligence as tax_agent
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import firm_time, harvest_guard, markets, tax_india as ti
from app.aurea_core import tax_intelligence
from app.aurea_core.rebalancing import Lot, Position, WashSaleGuard, optimise
from app.compliance.rules import CheckContext, india_harvest_guard, statutory_fifo
from app.conduit import execution
from app.core.db import Base
from app.models.corporate_actions import CorporateAction
from app.models.enums import AgentKey, HumanAction, MarketType
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pure() -> None:
    print("\n=== Holding periods, by the day ===")
    bought = date(2025, 10, 31)
    check("twelve months exactly is short-term", ti.bucket("listed_equity", bought, date(2026, 10, 31)), ti.ST_EQUITY)
    check("a day more is long-term", ti.bucket("listed_equity", bought, date(2026, 11, 1)), ti.LT_EQUITY)
    check("a leap day rolls to the month's end", ti.add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
    check("a REIT follows equity", ti.bucket("reit_invit", bought, date(2027, 1, 1)), ti.LT_EQUITY)
    check("a specified fund is never long-term", ti.bucket("specified_fund", date(2020, 1, 1), date(2026, 10, 31)), ti.ST_SLAB)
    check("and has no date it goes long", ti.long_term_from("specified_fund", bought), None)
    check("a gold ETF held a year and more", ti.bucket("other_listed", bought, date(2027, 1, 1)), ti.LT_OTHER)
    check("held less, at slab", ti.bucket("other_listed", bought, date(2026, 1, 1)), ti.ST_SLAB)
    check("an unlisted share needs 24 months", ti.bucket("unlisted", bought, date(2027, 1, 1)), ti.ST_SLAB)

    print("\n=== Set-off ===")
    r = ti.set_off({ti.ST_EQUITY: 100000, ti.LT_EQUITY: -50000})
    check("a long-term loss does not touch a short-term gain", r["net"][ti.ST_EQUITY], 100000)
    check("it carries forward instead", r["carry_forward"]["long_term"], 50000)
    r = ti.set_off({ti.LT_EQUITY: 200000, ti.ST_EQUITY: -50000})
    check("a short-term loss does touch a long-term gain", r["net"][ti.LT_EQUITY], 150000)
    check("the exemption comes after it", (r["exempt_used"], r["taxable_lt_equity"], r["exempt_left"]), (125000, 25000, 0))
    r = ti.set_off({ti.LT_EQUITY: 100000})
    check("part of the exemption can be left", r["exempt_left"], 25000)
    r = ti.set_off({ti.LT_EQUITY: 300000, ti.LT_OTHER: 100000, ti.LT_EQUITY + "_": 0} | {}, {"long_term": 100000})
    check("brought-forward goes first at what has no exemption", (r["net"][ti.LT_OTHER], r["net"][ti.LT_EQUITY]), (0, 300000))
    r = ti.set_off({ti.ST_EQUITY: 100000, ti.ST_SLAB: 100000, ti.LT_EQUITY: -0.0}, {"short_term": 60000}, slab_rate=0.3)
    check("a short-term loss goes at the higher rate first", (r["net"][ti.ST_SLAB], r["net"][ti.ST_EQUITY]), (40000, 100000))

    print("\n=== The tax on it ===")
    s = ti.set_off({ti.ST_EQUITY: 200000, ti.LT_EQUITY: 300000, ti.ST_SLAB: -50000, ti.LT_OTHER: -100000})
    t = ti.tax_on(s, slab_rate=0.3, income=1000000)
    check("20% and 12.5% with cess, no surcharge at this income", t["amount"], round((150000 * .2 + 75000 * .125) * 1.04, 2))
    t = ti.tax_on(ti.set_off({ti.LT_EQUITY: 225000}), slab_rate=0.3, income=30000000)
    check("long-term at the top is 14.95%", t["amount"], 14950.0)
    t = ti.tax_on(ti.set_off({ti.ST_EQUITY: 100000}), slab_rate=0.3, income=30000000)
    check("short-term at the top is 23.92%", t["amount"], 23920.0)
    t = ti.tax_on(ti.set_off({ti.ST_SLAB: 100000}), slab_rate=0.3, income=30000000)
    check("slab gains are not capped at 15%", t["amount"], 39000.0)
    t = ti.tax_on(ti.set_off({ti.ST_SLAB: 100000}), slab_rate=None, income=None)
    check("with no slab rate on file the gain is not guessed at", (t["amount"], t["incomplete"]), (0.0, ["slab_rate", "income"]))

    print("\n=== What a proposal adds ===")
    position = {"realised": {ti.LT_EQUITY: 100000}, "brought": {}, "slab_rate": 0.3, "income": 2000000, "exempt_left": 25000}
    p = ti.proposal_tax({ti.LT_EQUITY: 50000}, position)
    check("only what is over the exemption is taxed", p["estimated_tax"], round(25000 * .125 * 1.04, 2))
    check("and it says how much exemption it used", (p["exempt_used_by_this"], p["exempt_left_after"]), (25000, 0))

    print("\n=== The solver, under FIFO ===")
    today = date(2026, 10, 31)
    old, new = today - timedelta(days=800), today - timedelta(days=100)
    book = lambda: [
        Position("h1", "EQ", "EQ", "EQ", "equity", 40000, 100.0, 36000, "a1", "X",
                 lots=[Lot(200, 60.0, new, ti.ST_EQUITY), Lot(200, 120.0, old, ti.LT_EQUITY)]),
        Position("h2", "BD", "BD", "BD", "fixed_income", 10000, 100.0, 10000, "a1", "X",
                 lots=[Lot(100, 100.0, old, ti.LT_OTHER)]),
    ]
    target = {"equity": 0.5, "fixed_income": 0.5}
    fifo = optimise(positions=book(), target_weights=target, cash=0.0, fifo=True, whole_units=True)
    sell = fifo.orders[0]
    check("under FIFO the oldest lot goes first, loss or not", sell.est_realised_gain, round((100 - 120) * sell.quantity, 2))
    check("the gain is in its bucket", sell.gain_buckets, {ti.LT_EQUITY: round((100 - 120) * sell.quantity, 2)})
    check("and a demat holds whole shares", all(o.quantity == int(o.quantity) for o in fifo.orders), True)
    check("the totals follow", fifo.gains_by_bucket, sell.gain_buckets)

    gain_first = [Position("h1", "EQ", "EQ", "EQ", "equity", 40000, 100.0, 34000, "a1", "X",
                           lots=[Lot(200, 50.0, old, ti.LT_EQUITY), Lot(200, 120.0, new, ti.ST_EQUITY)]),
                  Position("h2", "BD", "BD", "BD", "fixed_income", 10000, 100.0, 10000, "a1", "X",
                           lots=[Lot(100, 100.0, old, ti.LT_OTHER)])]
    free = optimise(positions=gain_first, target_weights=target, cash=0.0)
    check("left to itself the solver goes to the loss", free.orders[0].est_realised_gain < 0, True)
    bound = optimise(positions=gain_first, target_weights=target, cash=0.0, fifo=True, whole_units=True)
    check("under FIFO it cannot: the gain in front goes first", bound.orders[0].est_realised_gain > 0, True)
    capped = optimise(positions=gain_first, target_weights=target, cash=0.0, fifo=True, whole_units=True, cgt_budget=1000)
    check("a budget stops the sale at the lot it runs out on", capped.orders[0].quantity, 20)
    check("and it cannot step over that lot to reach the loss behind it", capped.harvested_losses, 0)
    buckets = optimise(positions=gain_first, target_weights=target, cash=0.0, fifo=True, whole_units=True,
                       bucket_budgets={ti.LT_EQUITY: 500})
    check("a bucket has its own budget", buckets.gains_by_bucket[ti.LT_EQUITY], 500.0)

    window = {"on": "2026-08-01", "account": "Demat", "account_id": "a1", "clear_on": "2027-05-02", "say": "Stripped."}
    stripped = [Position("h1", "EQ", "EQ", "EQ", "equity", 40000, 100.0, 34000, "a1", "X",
                         lots=[Lot(100, 50.0, old, ti.LT_EQUITY), Lot(300, 120.0, new, ti.ST_EQUITY)]),
                Position("h2", "BD", "BD", "BD", "fixed_income", 10000, 100.0, 10000, "a1", "X",
                         lots=[Lot(100, 100.0, old, ti.LT_OTHER)])]
    held = optimise(positions=stripped, target_weights=target, cash=0.0, fifo=True, whole_units=True,
                    wash=WashSaleGuard(no_loss_sale={"EQ": window}))
    check("a stripped loss stops the walk at the gain before it", held.orders[0].quantity, 100)
    check("and the limitation is India's, not the wash-sale sentence", "Stripped." in held.limitations, True)

    barred = optimise(positions=book(), target_weights=target, cash=0.0, fifo=True, whole_units=True,
                      wash=WashSaleGuard(no_sell={"EQ": {**window, "say": "Bought today."}}))
    check("bought today is not sold today", [o.symbol for o in barred.orders if o.side == "sell"], [])
    check("and what was held is recorded", barred.wash_sale_held[0]["symbol"], "EQ")
    elsewhere = optimise(positions=book(), target_weights=target, cash=0.0, fifo=True, whole_units=True,
                         wash=WashSaleGuard(no_sell={"EQ": {**window, "account_id": "another-demat"}}))
    check("the rule is per demat", [o.symbol for o in elsewhere.orders if o.side == "sell"], ["EQ"])

    plain = optimise(positions=[Position("h", "EQ", "EQ", "EQ", "equity", 900, 100.0, 900, "a", "X", lots=[Lot(9, 100.0)]),
                                Position("b", "BD", "BD", "BD", "fixed_income", 100, 100.0, 100, "a", "X", lots=[Lot(1, 100.0)])],
                     target_weights=target, cash=0.0)
    check("with no market rules, quantities stay fractional and nothing is bucketed",
          (any(o.quantity != int(o.quantity) for o in plain.orders), plain.gains_by_bucket), (True, {}))
    short = optimise(positions=[Position("h", "EQ", "EQ", "EQ", "equity", 1000000, 1000.0, 500000, "a", "X",
                                         lots=[Lot(1000, 500.0, old, ti.LT_EQUITY)]),
                                Position("b", "BD", "BD", "BD", "fixed_income", 1000, 100.0, 1000, "a", "X",
                                         lots=[Lot(10, 100.0, old, ti.LT_OTHER)])],
                     target_weights=target, cash=0.0, fifo=True, whole_units=True,
                     cgt_budget=0, say_money=markets.money_for(type("F", (), {"base_currency": "INR"})))
    check("a limitation is in the firm's money", any("₹" in x and "$" not in x for x in short.limitations), True)

    print("\n=== The compliance rules read it ===")
    ctx = lambda p: CheckContext(agent_key="drift_rebalancing", rationale="", text="", confidence=1, payload=p,
                                 evidence={}, citations=[], mandate_type=None, policy={})
    check("FIFO passes", statutory_fifo(ctx({"india_tax": {"lot_method": "FIFO"}})).status, "pass")
    check("anything else fails", statutory_fifo(ctx({"india_tax": {"lot_method": "HIFO"}})).status, "fail")
    check("not India, not applicable", statutory_fifo(ctx({})).status, "na")
    check("a conflict fails the guard", india_harvest_guard(ctx({"india_tax": {"conflicts": [{"symbol": "X"}]}})).status, "fail")


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india", jurisdiction="IN", regulator="SEBI",
                        base_currency="INR", settings={"timezone": "Asia/Kolkata", "execution": {"lot_method": "HIFO", "fee_bps": 0, "fee_minimum": 0}})
            s.add(firm)
            await s.flush()
            today = firm_time.today(firm)
            year = markets.tax_year(markets.MARKETS["IN"], today)

            def inst(sym, cls, tax_class, close, *, real=True):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class=cls,
                               market_type=MarketType.PUBLIC, currency="INR", values_tags={"tax_class": tax_class})
                s.add(i)
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=close, currency="INR",
                            source="yahoo", is_real=real))
                return i

            infy, tcs, gold = inst("INFY.NS", "equity", "listed_equity", 1000), inst("TCS.NS", "equity", "listed_equity", 2000), inst("GOLDBEES.NS", "commodity", "other_listed", 100)
            bond = inst("GILT.NS", "fixed_income", "specified_fund", 100)
            old_stock = inst("ITC.NS", "equity", "listed_equity", 400)
            await s.flush()

            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Core", drift_band=0.05)
            s.add(model)
            await s.flush()
            s.add_all([TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=0.5),
                       TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="fixed_income",
                                        target_weight=0.5, instrument_id=bond.id)])
            family = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharma Family", values={})
            s.add(family)
            await s.flush()

            async def taxpayer(name, **tax):
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=family.id, full_name=name,
                           profile={"tax": tax} if tax else {})
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate",
                            mandate_type="advisory", model_portfolio_id=model.id,
                            suitability={"capacity_for_loss": "high"}, constraints={"cgt_budget": 500000})
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate=m, name=f"{name} demat", currency="INR",
                            custodian="CDSL", cash_balance=0)
                s.add_all([m, a])
                await s.flush()
                return p, m, a

            closes = {infy.id: 1000, tcs.id: 2000, gold.id: 100, bond.id: 100, old_stock.id: 400}

            async def hold(a, i, lots):
                h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=i.id,
                            quantity=sum(q for q, _, _ in lots), market_value=sum(q for q, _, _ in lots) * closes[i.id],
                            cost_basis=sum(q * c for q, c, _ in lots))
                s.add(h)
                await s.flush()
                for q, c, days in lots:
                    s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=q, cost_per_unit=c,
                                 acquired_on=today - timedelta(days=days)))
                await s.flush()
                return h

            rajesh, m_rajesh, a_rajesh = await taxpayer("Rajesh Sharma", regime="new", marginal_rate=0.30, annual_income=6000000)
            meera, m_meera, a_meera = await taxpayer("Meera Sharma", regime="new", marginal_rate=0.20, annual_income=1500000)
            sold = year["start"] if year["start"] <= today else today
            s.add(Transaction(firm_id=firm.id, account_id=a_rajesh.id, instrument_id=tcs.id, txn_type="sell",
                              quantity=2000, price=200, amount=400000, trade_date=sold,
                              lineage={"realised_gain": 200000, "fees": 0, "lots": [
                                  {"acquired_on": (sold - timedelta(days=800)).isoformat(), "quantity": 2000, "cost": 200000}]}))
            await hold(a_rajesh, tcs, [(200, 3000.0, 100)])                 # loss, at the front of the queue
            await hold(a_rajesh, infy, [(100, 800.0, 800), (50, 1300.0, 100)])
            await hold(a_rajesh, old_stock, [(100, 100.0, 4000)])           # before 31 Jan 2018
            await hold(a_meera, infy, [(300, 600.0, 800)])
            await hold(a_meera, gold, [(100, 80.0, 800)])
            await s.flush()

            print("\n=== The report: one family, two taxpayers ===")
            r = await tax_intelligence.for_household(s, family.id, "IN", firm_id=firm.id)
            check("India's report", (r["jurisdiction"], r["currency"], r["law"]), ("IN", "INR", "Income-tax Act 2025"))
            check("the tax year", r["tax_year"], year["label"])
            check("two taxpayers, not one household", [t["name"] for t in r["taxpayers"]], ["Meera Sharma", "Rajesh Sharma"])
            check("it says what it leaves out", "Marginal relief on surcharge" in r["not_modelled"], True)
            meera_r, rajesh_r = r["taxpayers"]
            check("Rajesh's gain is his", rajesh_r["realised"][ti.LT_EQUITY], 200000.0)
            check("Meera has none", meera_r["realised"][ti.LT_EQUITY], 0)
            check("the exemption is per person", (rajesh_r["position"]["exempt_left"], meera_r["position"]["exempt_left"]), (0, 125000))
            check("his tax is on what is over it, with surcharge",
                  rajesh_r["estimated_tax"], round(75000 * .125 * 1.10 * 1.04, 2))

            kinds = lambda t: sorted(o["kind"] for o in t["opportunities"])
            loss = next(o for o in rajesh_r["opportunities"] if o["kind"] == "loss")
            check("his loss at the front of the queue is harvestable", (loss["symbol"], loss["net_loss"]), ("TCS.NS", -200000.0))
            check("and it saves tax against his own gain", loss["tax_saved"] > 0, True)
            infy_loss = [o for o in rajesh_r["opportunities"] if o["symbol"] == "INFY.NS" and o["kind"] == "loss"]
            check("a loss behind a gain is not on offer: the gain goes first", infy_loss, [])
            check("Meera's exemption can use her own gain", "exemption" in kinds(meera_r), True)
            ex = next(o for o in meera_r["opportunities"] if o["kind"] == "exemption")
            check("to what the exemption covers, in whole shares", (ex["symbol"], ex["quantity"], ex["gain"]), ("INFY.NS", 300, 120000.0))
            check("a lot before 31 Jan 2018 is flagged, not counted",
                  rajesh_r["grandfathered"], [{"symbol": "ITC.NS", "account": "Rajesh Sharma demat", "quantity": 100.0}])
            check("the tally reads across the family", r["summary"]["taxpayers"], 2)

            print("\n=== Settlement ===")
            check("a firm setting cannot change the law", execution.lot_method_for_firm(firm), "fifo")
            nz = Firm(id=uuid.uuid4(), name="N", slug="n", jurisdiction="NZ", base_currency="NZD",
                      settings={"execution": {"lot_method": "HIFO"}})
            check("elsewhere it still can", execution.lot_method_for_firm(nz), "HIFO")

            print("\n=== The guard ===")
            g = await harvest_guard.guard(s, firm, m_rajesh, today=today)
            check("nothing today", (g["no_sell"], g["no_buy"]), ({}, {}))
            s.add(Transaction(firm_id=firm.id, account_id=a_meera.id, instrument_id=gold.id, txn_type="sell", quantity=1,
                              price=100, amount=100, trade_date=today, lineage={"realised_gain": -5000}))
            s.add(Transaction(firm_id=firm.id, account_id=a_meera.id, instrument_id=infy.id, txn_type="buy", quantity=1,
                              price=100, amount=100, trade_date=today, lineage={}))
            await s.flush()
            g = await harvest_guard.guard(s, firm, m_meera, today=today)
            check("sold today, not bought back today", g["no_buy"][str(gold.id)]["rule"], "same_day")
            check("it clears on a weekday", date.fromisoformat(g["no_buy"][str(gold.id)]["clear_on"]).weekday() < 5, True)
            check("bought today, not sold today", str(infy.id) in g["no_sell"], True)
            g2 = await harvest_guard.guard(s, firm, m_rajesh, today=today)
            check("the same-day rule is per demat: Rajesh's is untouched", g2["no_buy"], {})
            check("but the family's loss is watched from his account too", g2["rebuy_watch"][str(gold.id)]["account"], "Meera Sharma demat")

            order = lambda side, i, a, gain=0: {"side": side, "instrument_id": str(i.id), "symbol": i.symbol,
                                                 "account_id": str(a.id), "est_realised_gain": gain}
            check("a rebuy is flagged, and only flagged", [f["kind"] for f in harvest_guard.flags([order("buy", gold, a_rajesh)], g2)], ["rebuy_after_loss"])
            check("it is no conflict", harvest_guard.conflicts([order("buy", gold, a_rajesh)], g2), [])
            check("a same-day buy back is a conflict", [c["rule"] for c in harvest_guard.conflicts([order("buy", gold, a_meera)], g)], ["same_day"])

            record = today - timedelta(days=20)
            s.add(CorporateAction(firm_id=firm.id, instrument_id=tcs.id, action_type="bonus_issue", record_date=record,
                                  ex_date=record))
            await s.flush()
            for t in (await s.execute(select(TaxLot, Holding).join(Holding).where(
                    Holding.account_id == a_rajesh.id, Holding.instrument_id == tcs.id))).all():
                t[0].acquired_on = record - timedelta(days=30)
            await s.flush()
            g3 = await harvest_guard.guard(s, firm, m_rajesh, today=today)
            row = g3["no_loss_sale"].get(str(tcs.id)) or {}
            check("a purchase before a bonus record date is a stripping window", row.get("rule"), "bonus_stripping")
            check("a sale at a gain is not caught", harvest_guard.conflicts([order("sell", tcs, a_rajesh, 100)], g3), [])
            check("a sale at a loss is", [c["rule"] for c in harvest_guard.conflicts([order("sell", tcs, a_rajesh, -100)], g3)], ["bonus_stripping"])

            print("\n=== A proposal on an Indian book ===")
            async def _no_research(*a, **k):
                return []

            async def _fallback(ctx, *, fallback, **k):
                return fallback

            drift.knowledge.retrieve = _no_research
            drift.narrate = _fallback
            tax_agent.narrate = _fallback
            m_rajesh.constraints = {"cgt_budget": 5000000, "service_track": None}
            run = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING,
                                          subject=Subject("mandate", m_rajesh.id, m_rajesh.name), trigger="manual",
                                          require_human=True)
            await s.flush()
            from app.models.governance import Recommendation
            rec = (await s.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalar_one()
            tax = rec.payload["india_tax"]
            check("it carries the law and the year", (tax["lot_method"], tax["tax_year"]), ("FIFO", year["label"]))
            check("it knows whose tax this is", tax["taxpayer"], "Rajesh Sharma")
            check("gains are in buckets", set(tax["buckets"]) <= set(ti.BUCKETS), True)
            check("whole shares only", all(float(o["quantity"]).is_integer() for o in rec.payload["order_set"]), True)
            check("the summary is in rupees", "₹" in rec.summary and "$" not in rec.summary, True)
            check("the wash-sale rule stays American", rec.payload["wash_sale"]["applies"], False)
            check("the guard's answer stays on the proposal", isinstance(tax["benefit"], dict), True)

            print("\n=== The tax agent ===")
            agent = tax_agent.TaxIntelligenceAgent()
            from app.atlas.base import AgentContext
            ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject())
            drafts = await agent.think(ctx, await agent.sense(ctx))
            check("one proposal per household", len(drafts), 1)
            check("its title has no number that changes", drafts[0].title, "Tax position - The Sharma Family")
            check("and it is in rupees", "₹" in drafts[0].summary, True)

            print("\n=== Trading settles the Indian way ===")
            await s.execute(Price.__table__.update().values(is_real=True))
            result = await execution.execute_order_set(
                s, firm=firm, actor="adviser", mandate_id=m_rajesh.id, order_set=[{
                    "account_id": str(a_rajesh.id), "instrument_id": str(infy.id), "side": "sell", "quantity": 120,
                    "est_price": 1000, "est_value": 120000, "symbol": "INFY.NS", "asset_class": "equity"}])
            check("it settled", result["orders_settled"], 1)
            check("by FIFO, whatever the firm's setting says", result["lot_method"], "fifo")
            txn = (await s.execute(select(Transaction).where(Transaction.account_id == a_rajesh.id,
                                                              Transaction.instrument_id == infy.id))).scalars().first()
            lots = txn.lineage["lots"]
            check("the sale keeps the lots it relieved", [l["quantity"] for l in lots], [100.0, 20.0])
            check("oldest first", lots[0]["acquired_on"] < lots[1]["acquired_on"], True)
            check("on the firm's calendar", txn.trade_date, today)
            parts = ti._realised(txn, "listed_equity")
            check("so the report can read its buckets back",
                  (parts[ti.LT_EQUITY], parts[ti.ST_EQUITY]), (20000.0, -6000.0))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
