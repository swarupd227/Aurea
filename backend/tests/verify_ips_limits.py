"""Prove the rebalancer acts on an IPS limit, leaves what a person accepted and a sleeve it cannot fill, and that a
client sees what is on file for them and nobody else's.

Runs against a throwaway SQLite database.

    python -m tests.verify_ips_limits
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.drift_rebalancing import DriftRebalancingAgent
from app.api import canvas
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, ips, shelf
from app.aurea_core.rebalancing import Lot, Position, WashSaleGuard, optimise
from app.core.db import Base
from app.models.balance_sheet import BalanceItem
from app.models.enums import MarketType, UserRole
from app.models.engagement import Task
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, InvestmentPolicy, IpsException, ModelPortfolio, Price, TargetAllocation, TaxLot
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pos(symbol, cls, value, *, price=10.0, cost=8.0, tradeable=True, protected=False, excluded=False, account="a1") -> Position:
    qty = value / price
    return Position(holding_id=f"{symbol}-{account}", instrument_id=symbol, symbol=symbol, name=symbol, asset_class=cls, market_value=value, price=price,
                    cost_basis=qty * cost, account_id=account, custodian="C", tradeable=tradeable, protected=protected, excluded=excluded, lots=[Lot(qty, cost, acquired_on=date(2024, 1, 1))])


def sells(result) -> dict:
    return {o.symbol: round(o.est_value) for o in result.orders if o.side == "sell"}


def buys(result) -> dict:
    return {o.symbol: round(o.est_value) for o in result.orders if o.side == "buy"}


def pure() -> None:
    target = {"equity": 0.6, "fixed_income": 0.4}
    print("\n=== A holding over its limit ===")
    book = [pos("BIG", "equity", 40000), pos("OTH", "equity", 20000), pos("BND", "fixed_income", 20000), pos("GOV", "fixed_income", 20000)]
    plain = optimise(positions=book, target_weights=target, cash=0.0)
    check("in band and with no limit, nothing moves", (plain.needs_rebalance, plain.orders), (False, []))
    capped = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.30)
    check("past the limit it is sold down, class in band or not", sells(capped), {"BIG": 10000})
    check("that is a reason to act on its own", (capped.needs_rebalance, capped.drifted), (True, False))
    check("the order says why", capped.orders[0].reason, "Sell down to the IPS limit of 30%")
    check("within the band the money waits in cash", buys(capped), {})
    accepted = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.30, keep_over_limit={"BIG"})
    check("a limit a person accepted is left", accepted.orders, [])
    fund = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.30, funds={"BIG"})
    check("a diversified fund is not one issuer", fund.orders, [])
    split = optimise(positions=[pos("BIG", "equity", 25000), pos("BIG", "equity", 15000, account="a2"), pos("OTH", "equity", 20000), pos("BND", "fixed_income", 20000), pos("GOV", "fixed_income", 20000)],
                     target_weights=target, cash=0.0, max_position=0.30)
    check("a limit is on the instrument, across accounts", sum(sells(split).values()), 10000)
    tax = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.30, cgt_budget=500)
    check("the tax budget still binds", round(tax.estimated_realised_gain) <= 500, True)
    check("and what it left is said", any("could not be sold" in x for x in tax.limitations), True)

    print("\n=== A restricted holding ===")
    book = [pos("SIN", "equity", 20000), pos("OTH", "equity", 40000), pos("BND", "fixed_income", 40000)]
    out = optimise(positions=book, target_weights=target, cash=0.0, restricted={"SIN"})
    check("it goes, whole", sells(out), {"SIN": 20000})
    check("the money goes back to the class", list(buys(out)), ["OTH"])
    check("never back into it", "SIN" in buys(out), False)
    kept = optimise(positions=[pos("SIN", "equity", 20000, protected=True), pos("OTH", "equity", 40000), pos("BND", "fixed_income", 40000)],
                    target_weights=target, cash=0.0, restricted={"SIN"})
    check("an adviser's hold is respected, and said", (kept.orders, any("kept back" in x for x in kept.limitations)), ([], True))
    private = optimise(positions=[pos("PVT", "equity", 20000, tradeable=False), pos("OTH", "equity", 40000), pos("BND", "fixed_income", 40000)],
                       target_weights=target, cash=0.0, restricted={"PVT"})
    check("what cannot be traded is left, and said", (private.orders, any("not traded here" in x for x in private.limitations)), ([], True))
    held = optimise(positions=book, target_weights=target, cash=0.0, restricted={"SIN"},
                    wash=WashSaleGuard(no_sell={"SIN": {"say": "Bought today."}}))
    check("a sale the tax window bars is held, with no empty proposal", (held.orders, held.needs_rebalance), ([], False))

    print("\n=== Sold down and trimmed ===")
    both = optimise(positions=[pos("BIG", "equity", 80000), pos("BND", "fixed_income", 20000)], target_weights=target, cash=0.0, max_position=0.60)
    check("a position sold down is not sold twice", sells(both), {"BIG": 20000})
    check("and each lot is only sold once", sum(o.quantity for o in both.orders if o.side == "sell"), 2000.0)
    trim = optimise(positions=[pos("BIG", "equity", 90000), pos("BND", "fixed_income", 10000)], target_weights={"equity": 0.5, "fixed_income": 0.5}, cash=0.0, max_position=0.70)
    check("the class's trim adds only what is left", sum(v for o, v in [(o, o.est_value) for o in trim.orders if o.side == "sell"]), 40000)

    print("\n=== A sleeve the rebalancer cannot fill ===")
    private_pick = Position("", "PVT", "PVT", "Private Fund", "alternatives", 0.0, 100.0, 0.0, "", "", tradeable=False)
    growth = {"equity": 0.65, "fixed_income": 0.25, "alternatives": 0.10}
    held_book = [pos("EQ", "equity", 69000), pos("BND", "fixed_income", 31000)]
    quiet = optimise(positions=held_book, target_weights=growth, cash=0.0, model_instruments={"alternatives": private_pick}, drift_band=0.07)
    check("only the sleeve is out: no proposal", quiet.needs_rebalance, False)
    check("though the drift is still measured", quiet.max_drift, 0.10)
    public_pick = Position("", "ALT", "ALT", "Listed Alts", "alternatives", 0.0, 100.0, 0.0, "", "")
    live = optimise(positions=held_book, target_weights=growth, cash=0.0, model_instruments={"alternatives": public_pick}, drift_band=0.07)
    check("a sleeve that can be bought still counts", live.needs_rebalance, True)
    other = optimise(positions=[pos("EQ", "equity", 80000), pos("BND", "fixed_income", 20000)], target_weights=growth, cash=0.0, model_instruments={"alternatives": private_pick})
    check("real drift elsewhere still rebalances", other.needs_rebalance, True)
    check("and the sleeve is named on it", any("subscribed to outside the OMS" in x for x in other.limitations), True)


async def main() -> int:
    pure()
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
            h, h2 = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee"), Household(id=uuid.uuid4(), firm_id=firm.id, name="Cho")
            s.add_all([h, h2])
            await s.flush()
            ann, bo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann"), Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h2.id, full_name="Bo")
            s.add_all([ann, bo])
            await s.flush()
            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Core Growth", drift_band=0.05)
            s.add(model)
            await s.flush()
            s.add_all([TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=0.6),
                       TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="fixed_income", target_weight=0.4)])
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory", model_portfolio_id=model.id,
                        suitability={"risk_profile": "growth", "capacity_for_loss": "high"}, constraints={})
            m2 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=bo.id, name="Bo mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add_all([m, m2])
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann A/C", custodian="C", currency="USD", cash_balance=0)
            s.add(a)
            await s.flush()
            for symbol, cls, value in (("BIG", "equity", 40000), ("OTH", "equity", 20000), ("BND", "fixed_income", 20000), ("GOV", "fixed_income", 20000)):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class=cls, market_type=MarketType.PUBLIC, currency="USD")
                s.add(i)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=10, currency="USD", source="test", is_real=True))
                held = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=i.id, quantity=value / 10, market_value=value, cost_basis=value * 0.8)
                s.add(held)
                await s.flush()
                s.add(TaxLot(firm_id=firm.id, holding_id=held.id, quantity=value / 10, cost_per_unit=8, acquired_on=today - timedelta(days=500)))
            await s.flush()
            agent = DriftRebalancingAgent()
            ctx = AgentContext(session=s, firm=firm, run=None, subject=Subject("mandate", m.id, m.name), tier=None, guardrails=None, config={})

            async def proposal():
                sensed = await agent.sense(ctx)
                return await agent.think(ctx, sensed)

            print("\n=== The agent ===")
            check("no IPS and in band: nothing", await proposal(), [])
            policy = InvestmentPolicy(firm_id=firm.id, mandate_id=m.id, version=1, accepted_on=today, targets={"equity": 0.6, "fixed_income": 0.4}, drift_band=0.05,
                                      max_position=0.30, restricted=[], document="IPS v1", recorded_by="a@t", recorded_by_role="adviser")
            s.add(policy)
            await s.flush()
            drafted = (await proposal())[0]
            check("the IPS limit raises a proposal that says so", drafted.title, "Sell down Ann mandate: holdings outside its IPS")
            check("selling what is over", [(o["side"], o["symbol"]) for o in drafted.payload["order_set"]][0], ("sell", "BIG"))
            s.add(IpsException(firm_id=firm.id, mandate_id=m.id, policy_id=policy.id, term="position:BIG", excess=0.11, reason="Client's employer stock", until=today + timedelta(days=90), accepted_by="a@t"))
            await s.flush()
            check("accepted, it is left alone", await proposal(), [])
            check("the term is what was accepted", await ips.accepted_terms(s, policy, today), {"position:BIG"})
            await shelf.record_decision(s, firm.id, by="r@t", role="portfolio_team", symbol="OTH", status="approved", rationale="Index fund of 500 issuers", document="DD paper 2026", is_diversified=True)
            check("the research team says what is a fund", (await shelf.diversified(s, firm.id)) != set(), True)
            await shelf.record_decision(s, firm.id, by="r@t", role="portfolio_team", symbol="OTH", status="watch", rationale="Fees under review", document="DD paper 2026 B")
            check("and a later decision carries it", len(await shelf.diversified(s, firm.id)), 1)
            read = await ips.for_mandate(s, m.id, firm_id=firm.id)
            check("the monitor reads the fund the same way", "position:OTH" in [f["term"] for f in read["findings"] + read["excepted"]], False)

            print("\n=== A client sees what is on file ===")
            client = SimpleNamespace(role=UserRole.CLIENT, person_id=ann.id, firm_id=firm.id, email="c@t", full_name="Ann")
            client2 = SimpleNamespace(role=UserRole.CLIENT, person_id=bo.id, firm_id=firm.id, email="d@t", full_name="Bo")
            stranger = SimpleNamespace(role=UserRole.ADVISER, person_id=None, firm_id=other.id, email="x@o", full_name="X")
            s.add(BalanceItem(firm_id=firm.id, household_id=h.id, side="liability", kind="mortgage", name="Home loan", amount=30000, currency="USD", as_of=today, source_document="Bank letter 2026", recorded_by="adv@t"))
            await s.flush()
            sheet = await canvas.canvas_net_worth(household_id=None, user=client, firm=firm, db=s)
            check("their net worth", (sheet["net_worth"], sheet["basis"]), (70000.0, "assets_and_liabilities"))
            check("what is owed is listed", [i["name"] for i in sheet["items"]], ["Home loan"])
            check("not who typed it or from what", {"recorded_by", "source_document"} & set(sheet["items"][0]), set())
            terms = await canvas.canvas_ips(household_id=None, user=client, firm=firm, db=s)
            check("the terms they accepted", (terms["policies"][0]["version"], terms["policies"][0]["max_position"], terms["policies"][0]["targets"]["equity"]), (1, 0.3, 0.6))
            check("and no findings", {"findings", "excepted", "status"} & set(terms["policies"][0]), set())
            aimed = await canvas.canvas_net_worth(household_id=h.id, user=client2, firm=firm, db=s)
            check("naming another household does not help a client", (aimed["household_id"], aimed["net_worth"]), (str(h2.id), 0.0))
            none = await canvas.canvas_ips(household_id=None, user=client2, firm=firm, db=s)
            check("no IPS reads as none here", (none["policies"], none["without"]), ([], ["Bo mandate"]))
            try:
                await canvas.canvas_ips(household_id=h.id, user=stranger, firm=other, db=s)
                check("another firm's staff are refused", False, True)
            except HTTPException as exc:
                check("another firm's staff are refused", exc.status_code, 404)
            said = await canvas._plan_on_file(s, firm, h.id, lambda v: f"${v:,.0f}")
            check("the assistant is told the record", ("$70,000" in said, "IPS version 1" in said), (True, True))
            said = await canvas._plan_on_file(s, firm, h2.id, lambda v: f"${v:,.0f}")
            check("and what it does not mean", ("assets only" in said, "does not mean none exists" in said), (True, True))
            done = await canvas.submit_client_request(canvas.ClientRequestIn(request_type="correction", notes="The home loan is paid off"), user=client, firm=firm, db=s)
            row = await s.get(Task, uuid.UUID(done["task_id"]))
            check("a correction reaches the adviser, and changes nothing", (row.title, (await canvas.canvas_net_worth(household_id=None, user=client, firm=firm, db=s))["net_worth"]),
                  ("Client says something on file is wrong: The home loan is paid off", 70000.0))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
