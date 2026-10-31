"""Prove the licence decides what happens to a trade, and that an adviser's approval is not a client's
consent.

The same rebalance, on two tracks of one firm. On an Investment Adviser's track an approval places
nothing until the client's answer is on record, bound to those exact orders, and the answer executes
once. On a discretionary Portfolio Manager's track the approval is enough, and a person still gave it.
A firm with no licences on file is untouched.

Runs against a throwaway SQLite database; the model and the research store are stubbed.

    python -m tests.verify_licences
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from types import SimpleNamespace as N

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import drift_rebalancing as drift
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import consent, firm_time, licences
from app.compliance.rules import CheckContext, client_consent
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.consent import TradeConsent
from app.models.enums import AgentKey, AutonomyTier, HumanAction, MarketType, UserRole
from app.models.governance import LedgerEntry, Recommendation
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot
from app.models.tenant import AutonomyPolicy, Firm
from app.models.trading import Order
from app.provenance import policy

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pure() -> None:
    print("\n=== What each licence lets an agent do ===")
    ia, pms = licences.LICENCES["investment_adviser"], licences.LICENCES["pms_discretionary"]
    check("an adviser needs consent on every trade", (ia.may_execute, ia.per_trade_consent), (True, True))
    check("a discretionary manager does not", (pms.may_execute, pms.per_trade_consent), (True, False))
    check("a research analyst may not execute", licences.LICENCES["research_analyst"].may_execute, False)
    check("nor an advisory portfolio manager", licences.LICENCES["pms_advisory"].may_execute, False)

    print("\n=== A licence caps a tier ===")
    both = N(settings={"licences": ["investment_adviser", "pms_discretionary"]})
    ra = N(settings={"licences": ["research_analyst"]})
    check("no agent runs alone, even for a discretionary manager", licences.ceiling(both), AutonomyTier.TIER_2)
    check("a research analyst's agents only assist", licences.ceiling(ra), AutonomyTier.TIER_1)
    check("tier 3 is cut down", licences.cap(both, AutonomyTier.TIER_3), AutonomyTier.TIER_2)
    check("tier 1 is left alone: a cap never raises", licences.cap(both, AutonomyTier.TIER_1), AutonomyTier.TIER_1)
    check("the refusal names the licence", "Investment Adviser" in licences.refusal(both, AutonomyTier.TIER_3), True)
    check("within the cap there is none", licences.refusal(both, AutonomyTier.TIER_2), None)
    plain = N(settings={})
    check("a firm with no licences is not capped", (licences.ceiling(plain), licences.cap(plain, AutonomyTier.TIER_3)),
          (None, AutonomyTier.TIER_3))
    check("and has nothing to refuse", licences.refusal(plain, AutonomyTier.TIER_3), None)

    print("\n=== Which track a mandate is on ===")
    check("the mandate's own", licences.track_for(both, N(constraints={"service_track": "pms_discretionary"})).key,
          "pms_discretionary")
    check("a track the firm does not hold gets the strictest it does",
          licences.track_for(both, N(constraints={"service_track": "research_analyst"})).key, "investment_adviser")
    check("no track at all, the same", licences.track_for(both, N(constraints={})).key, "investment_adviser")
    check("a firm with no licences has no track", licences.track_for(plain, N(constraints={})), None)

    print("\n=== Consent is to these orders and no others ===")
    a = [{"instrument_id": "x", "side": "sell", "account_id": "1", "quantity": 10}]
    b = [{"instrument_id": "x", "side": "sell", "account_id": "1", "quantity": 11}]
    check("order does not matter", consent.digest(a + b), consent.digest(b + a))
    check("size does", consent.digest(a) == consent.digest(b), False)
    check("side does", consent.digest(a) == consent.digest([{**a[0], "side": "buy"}]), False)

    print("\n=== The compliance rule ===")
    ctx = lambda lic: CheckContext(agent_key="drift_rebalancing", rationale="", text="", confidence=1,
                                   payload={"licence": lic} if lic else {}, evidence={}, citations=[], mandate_type=None, policy={})
    check("held for consent passes", client_consent(ctx(licences.describe(ia))).status, "pass")
    check("no licence, not applicable", client_consent(ctx(None)).status, "na")
    check("a track that cannot execute fails", client_consent(ctx(licences.describe(licences.LICENCES["research_analyst"]))).status, "fail")


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            async def _no_research(*a, **k):
                return []

            async def _fallback(ctx, *, fallback, **k):
                return fallback

            drift.knowledge.retrieve = _no_research
            drift.narrate = _fallback

            firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                        settings={"timezone": "Asia/Kolkata", "demonstration": True,
                                  "licences": ["investment_adviser", "pms_discretionary"],
                                  "execution": {"fee_bps": 0, "fee_minimum": 0}})
            real_firm = Firm(id=uuid.uuid4(), name="Real", slug="real", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                             settings={"licences": ["investment_adviser"]})
            nz = Firm(id=uuid.uuid4(), name="Kiwi", slug="kiwi", jurisdiction="NZ", regulator="FMA", base_currency="NZD",
                      settings={"execution": {"fee_bps": 0, "fee_minimum": 0}})
            s.add_all([firm, real_firm, nz])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@x", full_name="A", hashed_password="x", role=UserRole.ADVISER)
            manager = User(id=uuid.uuid4(), firm_id=firm.id, email="p@x", full_name="P", hashed_password="x", role=UserRole.PORTFOLIO_TEAM)
            s.add_all([adviser, manager])
            await s.flush()

            async def book(f, name, *, track=None, mandate_type="advisory"):
                eq = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=f"EQ{uuid.uuid4().hex[:4]}", name="Equity", asset_class="equity",
                                market_type=MarketType.PUBLIC, currency=f.base_currency, values_tags={"tax_class": "listed_equity"})
                fi = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=f"FI{uuid.uuid4().hex[:4]}", name="Gilt", asset_class="fixed_income",
                                market_type=MarketType.PUBLIC, currency=f.base_currency, values_tags={"tax_class": "specified_fund"})
                s.add_all([eq, fi])
                await s.flush()
                for i in (eq, fi):
                    s.add(Price(firm_id=f.id, instrument_id=i.id, as_of=today, close=100, currency=f.base_currency,
                                source="yahoo", is_real=True))
                model = ModelPortfolio(id=uuid.uuid4(), firm_id=f.id, name=f"{name} model", drift_band=0.05)
                s.add(model)
                await s.flush()
                s.add_all([TargetAllocation(firm_id=f.id, model_id=model.id, asset_class="equity", target_weight=0.5, instrument_id=eq.id),
                           TargetAllocation(firm_id=f.id, model_id=model.id, asset_class="fixed_income", target_weight=0.5, instrument_id=fi.id)])
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name, values={})
                s.add(h)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=f"{name} client")
                s.add(p)
                await s.flush()
                constraints = {"cgt_budget": 9000000}
                if track:
                    constraints["service_track"] = track
                m = Mandate(id=uuid.uuid4(), firm_id=f.id, person_id=p.id, name=f"{name} mandate", mandate_type=mandate_type,
                            model_portfolio_id=model.id, suitability={"capacity_for_loss": "high"}, constraints=constraints)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=f.id, mandate_id=m.id, name=f"{name} demat", currency=f.base_currency,
                            custodian="CDSL", cash_balance=0)
                s.add(a)
                await s.flush()
                hold = Holding(id=uuid.uuid4(), firm_id=f.id, account_id=a.id, instrument_id=eq.id, quantity=1000,
                               market_value=100000, cost_basis=80000)
                s.add(hold)
                await s.flush()
                s.add(TaxLot(firm_id=f.id, holding_id=hold.id, quantity=1000, cost_per_unit=80, acquired_on=today - timedelta(days=800)))
                await s.flush()
                return m, a

            async def propose(f, m):
                run = await runtime.run_agent(s, firm=f, agent_key=AgentKey.DRIFT_REBALANCING,
                                              subject=Subject("mandate", m.id, m.name), trigger="manual", require_human=True)
                await s.flush()
                return (await s.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().first()

            async def orders(rec):
                return (await s.execute(select(func.count()).select_from(Order).where(Order.recommendation_id == rec.id))).scalar_one()

            async def approve(f, rec, who=adviser):
                return await runtime.decide(s, firm=f, recommendation=rec, action=HumanAction.APPROVE, actor_id=who.id,
                                            actor_label=who.email)

            async def answer(f, rec, given=True, channel="email", evidence="Email 31 Oct"):
                return await runtime.client_response(s, firm=f, recommendation=rec, given=given, channel=channel,
                                                     evidence_ref=evidence, note=None, actor_label="a@x")

            print("\n=== The tier a firm reads ===")
            s.add(AutonomyPolicy(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=None, tier=AutonomyTier.TIER_3))
            await s.flush()
            tier, _ = await policy.resolve_tier(s, firm.id, AgentKey.DRIFT_REBALANCING, None)
            check("a policy of tier 3 on a licensed firm reads tier 2", tier, AutonomyTier.TIER_2)
            s.add(AutonomyPolicy(firm_id=nz.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=None, tier=AutonomyTier.TIER_3))
            await s.flush()
            tier, _ = await policy.resolve_tier(s, nz.id, AgentKey.DRIFT_REBALANCING, None)
            check("on a firm with no licences it reads what was set", tier, AutonomyTier.TIER_3)

            print("\n=== Investment Adviser: approval places nothing ===")
            m_ia, a_ia = await book(firm, "Sharma", track="investment_adviser")
            rec = await propose(firm, m_ia)
            check("the proposal says which licence it is under", rec.payload["licence"]["key"], "investment_adviser")
            check("and that it needs consent", rec.payload["licence"]["per_trade_consent"], True)
            rec = await approve(firm, rec)
            check("it is approved", rec.status, "approved")
            check("no order was placed", await orders(rec), 0)
            state = rec.payload["client_consent"]
            check("it is waiting for the client", state["status"], "awaiting")
            check("for these orders", len(state["orders"]), len(rec.payload["order_set"]))

            print("\n=== While it waits ===")
            again = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING,
                                            subject=Subject("mandate", m_ia.id, m_ia.name), trigger="manual")
            check("the agent raises no second set", (await s.execute(
                select(func.count()).select_from(Recommendation).where(Recommendation.run_id == again.id))).scalar_one(), 0)
            view = await licences.overview(s, firm)
            check("it shows as awaited", [c["recommendation_id"] for c in view["awaiting_consent"]], [str(rec.id)])
            refused = await consent.blocked(s, firm=firm, rec=rec)
            check("the last line refuses too", "consent" in (refused or ""), True)
            agent = drift.DriftRebalancingAgent()
            from app.atlas.base import AgentContext
            ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={},
                               subject=Subject("mandate", m_ia.id, m_ia.name))
            out = await agent.act(ctx, rec)
            check("act refuses on its own account", (out["executed"], await orders(rec)), (False, 0))

            print("\n=== The client's answer ===")
            for bad, why in ((dict(channel="carrier pigeon"), "channel"), (dict(evidence=""), "evidence")):
                try:
                    await answer(firm, rec, **bad)
                    check(f"a bad {why} is refused", False, True)
                except consent.ConsentError:
                    check(f"a bad {why} is refused", True, True)
            rec = await answer(firm, rec)
            check("a yes places the orders", await orders(rec) > 0, True)
            check("and the book moved", rec.status, "executed")
            row = (await s.execute(select(TradeConsent).where(TradeConsent.recommendation_id == rec.id))).scalar_one()
            check("the answer is on record", (row.status, row.channel, row.evidence_ref, row.recorded_by, row.simulated),
                  ("given", "email", "Email 31 Oct", "a@x", False))
            placed = await orders(rec)
            try:
                await answer(firm, rec)
                check("a second answer is refused", False, True)
            except (runtime.AlreadyDecidedError, consent.ConsentError):
                check("a second answer is refused", True, True)
            check("and places nothing", await orders(rec), placed)
            entry = (await s.execute(select(LedgerEntry).where(LedgerEntry.event_type == "client_consent",
                                                                LedgerEntry.recommendation_id == rec.id))).scalar_one()
            check("the ledger has the answer, and what it covered",
                  (entry.content["answer"], entry.content["channel"], len(entry.content["orders"]) > 0), ("given", "email", True))

            print("\n=== A no ===")
            m2, _ = await book(firm, "Iyer", track="investment_adviser")
            r2 = await approve(firm, await propose(firm, m2))
            r2 = await answer(firm, r2, given=False)
            check("closes the proposal", (r2.status, await orders(r2)), ("dismissed", 0))

            print("\n=== Changed orders ===")
            m3, _ = await book(firm, "Menon", track="investment_adviser")
            r3 = await approve(firm, await propose(firm, m3))
            changed = [{**o, "quantity": o["quantity"] - 1} for o in r3.payload["order_set"]]
            r3.modified_payload = {**r3.payload, "order_set": changed}
            await s.flush()
            try:
                await answer(firm, r3)
                check("a consent does not cover different orders", False, True)
            except consent.ConsentError:
                check("a consent does not cover different orders", True, True)
            check("nothing was placed", await orders(r3), 0)

            print("\n=== Simulated answers ===")
            m4, _ = await book(firm, "Rao", track="investment_adviser")
            r4 = await approve(firm, await propose(firm, m4))
            r4 = await answer(firm, r4, channel="simulated", evidence=None)
            row = (await s.execute(select(TradeConsent).where(TradeConsent.recommendation_id == r4.id))).scalar_one()
            check("a demonstration firm may simulate one, and it says so", (row.simulated, row.channel), (True, "simulated"))
            check("on the proposal too", r4.payload["client_consent"]["simulated"], True)
            try:
                consent.check_answer(real_firm, channel="simulated", evidence_ref=None)
                check("no other firm may", False, True)
            except consent.ConsentError:
                check("no other firm may", True, True)

            print("\n=== Rolled back while waiting ===")
            m5, _ = await book(firm, "Das", track="investment_adviser")
            r5 = await approve(firm, await propose(firm, m5))
            r5 = await runtime.rollback(s, firm=firm, recommendation=r5, actor_id=adviser.id, actor_label="a@x")
            try:
                await answer(firm, r5)
                check("an answer after a rollback is refused", False, True)
            except runtime.AlreadyDecidedError:
                check("an answer after a rollback is refused", True, True)

            print("\n=== Discretionary Portfolio Manager ===")
            m6, a6 = await book(firm, "Kapoor", track="pms_discretionary", mandate_type="discretionary")
            r6 = await propose(firm, m6)
            check("the proposal says it needs no client consent", r6.payload["licence"]["per_trade_consent"], False)
            check("but it stops at a proposal: a person decides", r6.status, "proposed")
            r6 = await approve(firm, r6, manager)
            check("the portfolio team's approval places the orders", (r6.status, await orders(r6) > 0), ("executed", True))
            check("no consent is asked for", (await s.execute(select(TradeConsent).where(TradeConsent.recommendation_id == r6.id))).scalar_one_or_none(), None)

            print("\n=== A track that cannot execute ===")
            ra_firm = Firm(id=uuid.uuid4(), name="RA", slug="ra", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                           settings={"licences": ["research_analyst"], "execution": {"fee_bps": 0, "fee_minimum": 0}})
            s.add(ra_firm)
            await s.flush()
            ra_user = User(id=uuid.uuid4(), firm_id=ra_firm.id, email="r@x", full_name="R", hashed_password="x", role=UserRole.ADVISER)
            s.add(ra_user)
            await s.flush()
            m7, _ = await book(ra_firm, "Nair", track="research_analyst")
            r7 = await approve(ra_firm, await propose(ra_firm, m7), ra_user)
            check("approving places nothing, and no consent is sought",
                  (r7.payload["client_consent"]["status"], await orders(r7)), ("not_permitted", 0))
            check("and it says why", "may not execute" in r7.payload["execution_result"]["note"], True)

            print("\n=== A direct order ===")
            tools = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            sym = (await s.execute(select(Instrument.symbol).where(Instrument.firm_id == firm.id))).scalars().first()
            try:
                await tools.execute_orders({"mandate_id": str(m_ia.id), "orders": [{"symbol": sym, "side": "buy", "quantity": 1}]})
                check("an adviser's mandate cannot take one", False, True)
            except ExecutorError as exc:
                check("an adviser's mandate cannot take one", "consent" in str(exc), True)

            print("\n=== Through the tools ===")
            m9, _ = await book(firm, "Bose", track="investment_adviser")
            r9 = await approve(firm, await propose(firm, m9))
            seen = await tools.read_licences({})
            check("the conversation can see what is waiting", str(r9.id) in [c["recommendation_id"] for c in seen["awaiting_consent"]], True)
            done = await tools.record_client_consent({"recommendation_id": str(r9.id), "given": True, "channel": "recorded_call",
                                                      "evidence_ref": "Call 31 Oct 16:02"})
            check("and record the answer", (done["status"], done["client_consent"]["channel"]), ("executed", "recorded_call"))
            try:
                await ToolExecutors(s, adviser.id, UserRole.OPERATIONS, firm.id).record_client_consent(
                    {"recommendation_id": str(r9.id), "given": True, "channel": "email", "evidence_ref": "x"})
                check("operations may not", False, True)
            except ExecutorError:
                check("operations may not", True, True)

            print("\n=== A firm with no licences ===")
            m8, _ = await book(nz, "Chen")
            r8 = await approve(nz, await propose(nz, m8))
            check("is untouched: approval trades", (r8.status, "client_consent" in r8.payload), ("executed", False))
            check("and no licence is written on the proposal", "licence" in r8.payload, False)
            check("its overview says none apply", (await licences.overview(s, nz))["applies"], False)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
