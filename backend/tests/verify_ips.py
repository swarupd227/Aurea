"""Prove IPS monitoring: the client's accepted terms outrank the model, only a person records them, an
approval never trades to terms that have changed, a breach is put to a person and accepted only by one,
and a firm sees only its own.

Runs the real rebalancer and the real monitor through the real runtime against a throwaway SQLite database.

    python -m tests.verify_ips
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import drift_rebalancing as drift
from app.agents import ips_drafting, schedules
from app.agents.catalogue import CATALOGUE
from app.agents.ips_monitoring import IpsMonitoringAgent
from app.api import ips as api
from app.atlas import runtime
from app.atlas.base import AgentContext, Subject
from app.atlas.registry import all_agents
from app.aurea_core import firm_time, ips, risk_profile as rp
from app.aurea_core.analytics.portfolio import _drift_summary
from app.aurea_core.rebalancing import Lot, Position, optimise
from app.core import agent_runs, decision_rights
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState, anthropic_tool_schema, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentKey, AutonomyTier, HumanAction, MarketType, RecommendationStatus, UserRole
from app.models.governance import LedgerEntry, Recommendation
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase
from app.models.portfolio import (Holding, Instrument, InvestmentPolicy, IpsException, ModelPortfolio, Price,
                                  TargetAllocation, TaxLot)
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm
from seed import ips_backfill

PASS, FAIL = [], []
KEY = AgentKey.IPS_MONITORING
TERMS = {"targets": {"equity": 0.6, "fixed_income": 0.4}, "drift_band": 0.05, "max_position": None, "min_cash": None,
         "profile": {"risk_profile": "balanced", "capacity_for_loss": "medium"}}
SAME = {"risk_profile": "balanced", "capacity_for_loss": "medium"}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except (ips.IpsError, rp.RiskProfileError, ExecutorError) as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def forbidden(label: str, coro, code: int = 403) -> None:
    try:
        await coro
    except HTTPException as e:
        check(label, e.status_code, code)
    else:
        check(label, "allowed", code)


def rules() -> None:
    print("\n=== Who runs it, who decides, when it runs ===")
    check("the monitor is registered", KEY in all_agents(), True)
    check("an adviser, the portfolio team and compliance may start it",
          sorted(r.value for r in agent_runs.run_roles(KEY)), ["adviser", "compliance", "portfolio_team"])
    check("a run from a sentence stops at a proposal", agent_runs.forced_to_proposal(KEY), True)
    check("even at Tier 3 it does not act on its own", agent_runs.acts_on_its_own(KEY, "tier_3"), False)
    check("an adviser or the portfolio team accepts a breach",
          sorted(r.value for r in decision_rights.roles_for(KEY, "approve")), ["adviser", "portfolio_team"])
    for role in (UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.ADMIN):
        try:
            decision_rights.check(role, KEY, "approve")
            check(f"{role.value} may not accept one", False, True)
        except decision_rights.DecisionForbidden as e:
            check(f"{role.value} may not accept one", "breach of a client's IPS" in str(e), True)
    decision_rights.check(UserRole.COMPLIANCE, KEY, "dismiss")
    check("compliance may dismiss and sees it as its veto", decision_rights.queue_action(UserRole.COMPLIANCE, KEY), "veto")
    check("an adviser is asked to decide", decision_rights.queue_action(UserRole.ADVISER, KEY), "decide")
    check("it runs firm-wide", CATALOGUE[KEY]["subject"], "firm")
    check("on business-day mornings, after the price run", schedules.default_for(KEY)[0], "45 6 * * 1-5")
    check("every other agent's rights are unchanged", decision_rights.roles_for(AgentKey.CLIENT_CARE, "approve"), decision_rights.STAFF)

    print("\n=== The tools ===")
    record = TOOLS["record_ips"]
    check("recording stops at a Confirm card", record.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("only an adviser records one", record.roles_required, {UserRole.ADVISER})
    args = {"mandate_id": str(uuid.uuid4()), "accepted_on": "2026-01-01", "document": "Signed IPS"}
    check("a paraplanner may not", validate_tool_call(UserRole.PARAPLANNER, "record_ips", args)[0], False)
    check("a client cannot read it", validate_tool_call(UserRole.CLIENT, "read_ips_monitoring", {})[0], False)
    props = anthropic_tool_schema(record)["input_schema"]["properties"]
    check("targets reach the model as a list", (props["targets"]["type"], props["targets"]["items"]["type"]), ("array", "string"))
    check("restricted too", props["restricted"]["type"], "array")
    check("reading is not a change", TOOLS["read_ips_monitoring"].change_state, ToolChangeState.NO)


def pure() -> None:
    print("\n=== Reading a book against terms ===")
    def found(weights, positions=(), profile=SAME, **terms):
        return ips.findings_of({**TERMS, **terms}, weights=weights, positions=list(positions), profile=profile)

    check("inside the band is nothing", found({"equity": 0.64, "fixed_income": 0.36}), [])
    over = found({"equity": 0.66, "fixed_income": 0.34})
    check("past it is a breach on each side", [f["term"] for f in over], ["allocation:equity", "allocation:fixed_income"])
    check("with the range it was held to", (over[0]["low"], over[0]["high"], over[0]["weight"]), (0.55, 0.65, 0.66))
    check("and how far outside", over[0]["excess"], 0.01)
    check("a class with no target counts as all drift", [f["term"] for f in found({"equity": 0.6, "fixed_income": 0.33, "property": 0.07})],
          ["allocation:fixed_income", "allocation:property"])
    check("cash is outside the band, as in the rebalancer", found({"equity": 0.6, "fixed_income": 0.3, "cash": 0.1})[0]["term"], "allocation:fixed_income")
    check("cash answers to its floor", [f["term"] for f in found({"equity": 0.61, "fixed_income": 0.38, "cash": 0.01}, min_cash=0.02)], ["cash"])
    check("no floor, no finding", found({"equity": 0.61, "fixed_income": 0.39}), [])
    pos = [{"symbol": "AAA", "weight": 0.3, "restricted": False}]
    check("a position over its limit", [(f["term"], f["excess"]) for f in found({"equity": 0.6, "fixed_income": 0.4}, pos, max_position=0.25)],
          [("position:AAA", 0.05)])
    check("at its limit is fine", found({"equity": 0.6, "fixed_income": 0.4}, pos, max_position=0.3), [])
    check("no limit, no finding", found({"equity": 0.6, "fixed_income": 0.4}, pos), [])
    check("a restricted holding, however small", [f["term"] for f in found({"equity": 0.6, "fixed_income": 0.4},
          [{"symbol": "BTI", "weight": 0.001, "restricted": True}])], ["restricted:BTI"])
    check("a profile changed since acceptance", [f["kind"] for f in found({"equity": 0.6, "fixed_income": 0.4},
          profile={"risk_profile": "conservative", "capacity_for_loss": "medium"})], ["profile"])
    check("a capacity changed too", [f["kind"] for f in found({"equity": 0.6, "fixed_income": 0.4},
          profile={"risk_profile": "balanced", "capacity_for_loss": "low"})], ["profile"])

    print("\n=== What the rebalancer does with a floor and a limit ===")
    def position(sym, cls, value):
        return Position(holding_id=sym, instrument_id=sym, symbol=sym, name=sym, asset_class=cls, market_value=value,
                        price=100.0, cost_basis=value, account_id="A", custodian="C", lots=[Lot(value / 100.0, 100.0)])

    book = [position("EQ", "equity", 8000.0), position("BND", "fixed_income", 2000.0)]
    target = {"equity": 0.6, "fixed_income": 0.4}
    plain = optimise(positions=book, target_weights=target, cash=0.0)
    check("without either, the buy takes all the proceeds", plain.orders[-1].est_value > 1900, True)
    held = optimise(positions=book, target_weights=target, cash=0.0, min_cash=0.1)
    sold = sum(o.est_value for o in held.orders if o.side == "sell")
    bought = sum(o.est_value for o in held.orders if o.side == "buy")
    check("the floor stays in cash", sold - bought >= 1000.0, True)
    check("and it says why the buy is short", any("floor in the IPS" in x for x in held.limitations), True)
    capped = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.25)
    buys = [o for o in capped.orders if o.side == "buy"]
    check("a buy stops at the position limit", buys[0].est_value <= 500.0, True)
    check("and names what is left to place", any("limited to 25%" in x for x in capped.limitations), True)
    check("which is not reported as a funding shortfall", any(" short." in x for x in capped.limitations), False)
    full = optimise(positions=book, target_weights=target, cash=0.0, max_position=0.2)
    check("already at the limit, no buy at all", [o.side for o in full.orders], ["sell"])
    check("without terms nothing changes", (plain.max_drift, len(plain.orders)),
          (optimise(positions=book, target_weights=target, cash=0.0, min_cash=None, max_position=None).max_drift, 2))


async def main() -> int:
    rules()
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            quiet = Firm(id=uuid.uuid4(), name="Q", slug="q", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other, quiet])
            await s.flush()
            today = firm_time.today(firm)

            def person(role, name, f=firm):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name}@t", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u

            sophie = person(UserRole.ADVISER, "sophie")
            priya = person(UserRole.PARAPLANNER, "priya")
            david = person(UserRole.COMPLIANCE, "david")
            research = person(UserRole.RESEARCH_CIO, "rae")
            client = person(UserRole.CLIENT, "wei")
            outsider = person(UserRole.ADVISER, "ola", other)
            quiet_adviser = person(UserRole.ADVISER, "quinn", quiet)
            await s.flush()

            def inst(f, sym, cls, **tags):
                i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=sym, name=sym, asset_class=cls,
                               market_type=MarketType.PUBLIC, currency="NZD", values_tags=tags)
                s.add(i)
                s.add(Price(firm_id=f.id, instrument_id=i.id, as_of=today, close=100.0, currency="NZD", source="yahoo", is_real=True))
                return i

            eq, bnd, bti = inst(firm, "EQ", "equity"), inst(firm, "BND", "fixed_income"), inst(firm, "BTI", "equity", flags=["tobacco"])
            q_eq, q_bnd = inst(quiet, "EQ", "equity"), inst(quiet, "BND", "fixed_income")
            await s.flush()

            async def model_for(f, equity, fixed, stock, bond, band=0.05, name="Model"):
                m = ModelPortfolio(id=uuid.uuid4(), firm_id=f.id, name=name, drift_band=band)
                s.add(m)
                await s.flush()
                s.add_all([TargetAllocation(firm_id=f.id, model_id=m.id, asset_class="equity", target_weight=equity, instrument_id=stock.id),
                           TargetAllocation(firm_id=f.id, model_id=m.id, asset_class="fixed_income", target_weight=fixed, instrument_id=bond.id)])
                await s.flush()
                return m

            model = await model_for(firm, 0.6, 0.4, eq, bnd, name="60/40")
            cautious = await model_for(firm, 0.3, 0.7, eq, bnd, name="30/70")
            q_model = await model_for(quiet, 0.5, 0.5, q_eq, q_bnd, band=0.10, name="50/50")

            async def client_of(name, f=firm, the_model=model, capacity="medium", profile="balanced", exclude=None, constraints=None):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name)
                s.add(h)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=name)
                s.add(p)
                await s.flush()
                suit = {"risk_profile": profile, "capacity_for_loss": capacity}
                if exclude:
                    suit["values_exclusions"] = exclude
                m = Mandate(id=uuid.uuid4(), firm_id=f.id, person_id=p.id, name=f"{name} mandate", mandate_type="discretionary",
                            suitability=suit, constraints=constraints or {},
                            model_portfolio_id=the_model.id if the_model else None)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=f.id, mandate_id=m.id, name=f"{name} A/C", currency="NZD", cash_balance=0)
                s.add(a)
                await s.flush()
                return h, m, a

            async def holding(a, i, value):
                h = Holding(id=uuid.uuid4(), firm_id=a.firm_id, account_id=a.id, instrument_id=i.id, quantity=value / 100.0,
                            market_value=value, cost_basis=value)
                s.add(h)
                await s.flush()
                s.add(TaxLot(firm_id=a.firm_id, holding_id=h.id, quantity=value / 100.0, cost_per_unit=100.0,
                             acquired_on=today - timedelta(days=400)))
                await s.flush()
                return h

            async def seen(m, f=firm):
                return await ips.for_mandate(s, m.id, firm_id=f.id)

            def write(m, f=firm, **kw):
                args = {"accepted_on": today - timedelta(days=10), "document": "Signed IPS, 21 Oct", "by": "sophie@t", "role": "adviser", **kw}
                return ips.record(s, m.id, firm_id=f.id, **args)

            def prepare(m, f=firm, **kw):
                args = {"accepted_on": today - timedelta(days=10), "document": "Signed IPS, 21 Oct", **kw}
                return ips.prepare_record(s, m.id, firm_id=f.id, **args)

            async def ledger(event):
                return (await s.execute(select(func.count()).select_from(LedgerEntry).where(
                    LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == event))).scalar()

            print("\n=== No IPS on file ===")
            ann, ann_m, ann_a = await client_of("Ann", exclude=["BTI"])
            ann_eq = await holding(ann_a, eq, 6000.0)
            ann_bnd = await holding(ann_a, bnd, 4000.0)
            v = await seen(ann_m)
            check("it says so, and is not a breach", (v["status"], v["ips"], v["findings"]), ("no_ips", None, []))
            check("it is measured against its model", (v["measured_against"], v["targets"], v["drift_band"]),
                  ("model", {"equity": 0.6, "fixed_income": 0.4}, 0.05))
            check("and reports how it sits against that", v["model_drift"], {"max_drift": 0.0, "band": 0.05, "outside": False})
            bare_h, bare_m, _ = await client_of("Bare", the_model=None)
            v = await seen(bare_m)
            check("no IPS and no model measures against nothing", (v["status"], v["measured_against"], v["model_drift"]), ("no_ips", None, None))

            print("\n=== Refused before anything is written ===")
            await refused("a date not given", prepare(ann_m, accepted_on=None), "date the client accepted")
            await refused("a date to come", prepare(ann_m, accepted_on=today + timedelta(days=1)), "before it has been accepted")
            await refused("no document", prepare(ann_m, document="  "), "which document")
            await refused("targets short of 100%", prepare(ann_m, targets={"equity": 0.5, "fixed_income": 0.4}), "add up to 100%")
            await refused("targets over 100%", prepare(ann_m, targets={"equity": 0.7, "fixed_income": 0.4}), "add up to 100%")
            await refused("an unknown class", prepare(ann_m, targets={"crypto": 1.0}), "unknown asset class")
            await refused("a negative weight", prepare(ann_m, targets={"equity": -0.1, "fixed_income": 1.1}), "between 0%")
            await refused("a band of nothing", prepare(ann_m, drift_band=0), "drift band")
            await refused("a band too wide", prepare(ann_m, drift_band=0.3), "drift band")
            await refused("a limit of nothing", prepare(ann_m, max_position=0), "position limit")
            await refused("a limit that is not a number", prepare(ann_m, max_position="lots"), "must be a number")
            await refused("no model and no targets", prepare(bare_m), "no model")
            await refused("another firm's mandate", prepare(ann_m, f=other), "not found")
            high_h, high_m, _ = await client_of("Low cap", capacity="low")
            await refused("equity over the client's ceiling", prepare(high_m), "above the 40% ceiling")
            ok = await prepare(high_m, targets={"equity": 0.4, "fixed_income": 0.6})
            check("at the ceiling is fine", ok["targets"]["equity"], 0.4)
            inactive_h, inactive_m, _ = await client_of("Gone")
            inactive_m.is_active = False
            await refused("an inactive mandate", prepare(inactive_m), "not found")
            check("nothing was written", (await s.execute(select(func.count()).select_from(InvestmentPolicy))).scalar(), 0)

            print("\n=== Recording ===")
            ready = await prepare(ann_m)
            check("left out, the targets and band are the model's", (ready["targets"], ready["drift_band"]),
                  ({"equity": 0.6, "fixed_income": 0.4}, 0.05))
            check("the restrictions are the mandate's exclusions", ready["restricted"], ["BTI"])
            check("the limits have no default", (ready["max_position"], ready["min_cash"]), (None, None))
            out = await write(ann_m)
            check("it is version 1", (out["version"], out["ips"]["version"], out["ips"]["source"]), (1, 1, "recorded"))
            check("the mandate is now measured against it", (out["status"], out["measured_against"]), ("within", "ips"))
            check("the ledger has it", await ledger("ips_recorded"), 1)
            check("who recorded it stays with it", (out["ips"]["recorded_by"], out["ips"]["recorded_by_role"]), ("sophie@t", "adviser"))
            check("as does what the mandate said then", out["ips"]["profile"], SAME)

            await refused("a replacement needs a reason", prepare(ann_m, drift_band=0.04), "give the reason")
            await refused("the same terms again", prepare(ann_m, reason="Reviewed"), "terms of version 1")
            await refused("a version cannot predate the last", prepare(ann_m, drift_band=0.04, reason="Reviewed",
                                                                       accepted_on=today - timedelta(days=11)), "cannot predate")
            out = await write(ann_m, drift_band=0.04, reason="Tighter band agreed", accepted_on=today - timedelta(days=5))
            check("a change is a new version", (out["version"], out["drift_band"]), (2, 0.04))
            check("and the old one stays", [(h["version"], h["drift_band"]) for h in out["history"]], [(2, 0.04), (1, 0.05)])
            check("the highest version is in force", (await ips.in_force(s, ann_m.id)).version, 2)

            print("\n=== The book against the terms ===")
            ann_eq.market_value = 8000
            v = await seen(ann_m)
            check("drift past the band is a breach", (v["status"], [f["term"] for f in v["findings"]]),
                  ("breach", ["allocation:equity", "allocation:fixed_income"]))
            ann_eq.market_value = 6000
            await holding(ann_a, bti, 100.0)
            v = await seen(ann_m)
            check("a restricted holding is a breach, by symbol", [f["term"] for f in v["findings"]], ["restricted:BTI"])
            flag_h, flag_m, flag_a = await client_of("Flag")
            await holding(flag_a, eq, 5900.0)
            await holding(flag_a, bnd, 4000.0)
            await holding(flag_a, bti, 100.0)
            v = await write(flag_m, restricted=["tobacco"])
            check("or by values flag", [f["term"] for f in v["findings"]], ["restricted:BTI"])
            lim_h, lim_m, lim_a = await client_of("Limits")
            await holding(lim_a, eq, 6000.0)
            await holding(lim_a, bnd, 4000.0)
            v = await write(lim_m, max_position=0.5, min_cash=0.02)
            check("a position and a cash floor", sorted(f["term"] for f in v["findings"]), ["cash", "position:EQ"])
            none_h, none_m, _ = await client_of("Empty")
            v = await write(none_m)
            check("an IPS with nothing to measure is not assessed", (v["status"], v["findings"]), ("not_assessed", []))
            syn_h, syn_m, syn_a = await client_of("Syn")
            await holding(syn_a, eq, 6000.0)
            await holding(syn_a, bnd, 4000.0)
            await write(syn_m)
            (await s.execute(select(Price).where(Price.instrument_id == bnd.id))).scalar_one().is_real = False
            check("a synthetic price is said", (await seen(syn_m))["price_source"], "synthetic")
            (await s.execute(select(Price).where(Price.instrument_id == bnd.id))).scalar_one().is_real = True

            print("\n=== An exception ===")
            ex_h, ex_m, ex_a = await client_of("Exc")
            ex_eq = await holding(ex_a, eq, 6000.0)
            await holding(ex_a, bnd, 4000.0)
            await write(ex_m, max_position=0.5)
            done = await ips.accept(s, ex_m, firm=firm, terms=["position:EQ", "cash"], reason="Held for tax", by="sophie@t", recommendation_id=None)
            check("what stands is accepted, what does not is reported", (done["accepted"], done["moved_on"]), (["position:EQ"], ["cash"]))
            check("for a limited time", done["until"], (today + timedelta(days=ips.EXCEPTION_DAYS)).isoformat())
            v = await seen(ex_m)
            check("the mandate reads excepted", (v["status"], v["findings"]), ("excepted", []))
            check("and says why, who and until when", (v["excepted"][0]["reason"], v["excepted"][0]["accepted_by"], v["excepted"][0]["until"]),
                  ("Held for tax", "sophie@t", done["until"]))
            check("the ledger has it", await ledger("ips_exception_accepted"), 1)
            ex_eq.market_value = 6200
            check("a breach that barely moves stays covered", (await seen(ex_m))["status"], "excepted")
            ex_eq.market_value = 7500
            v = await seen(ex_m)
            check("one that has grown is open again", (v["status"], "position:EQ" in [f["term"] for f in v["findings"]]), ("breach", True))
            ex_eq.market_value = 6000
            row = (await s.execute(select(IpsException).where(IpsException.mandate_id == ex_m.id))).scalar_one()
            row.until = today - timedelta(days=1)
            check("a lapsed exception covers nothing", (await seen(ex_m))["status"], "breach")
            row.until = today
            check("it runs through its last day", (await seen(ex_m))["status"], "excepted")
            await write(ex_m, max_position=0.55, reason="Limit reviewed")
            check("a new version leaves it behind", (await seen(ex_m))["status"], "breach")

            print("\n=== The rebalancer works to the IPS ===")
            async def _no_research(*a, **k):
                return []

            async def _fallback(ctx, *, fallback, **k):
                return fallback

            drift.knowledge.retrieve = _no_research
            drift.narrate = _fallback
            ips_drafting.narrate = _fallback

            def ctx_for(m, f=firm):
                return AgentContext(session=s, firm=f, run=None, tier=AutonomyTier.TIER_2, guardrails={}, config={},
                                    subject=Subject("mandate", m.id, m.name))

            async def propose(m):
                agent = drift.DriftRebalancingAgent()
                c = ctx_for(m)
                return await agent.think(c, await agent.sense(c))

            bob_h, bob_m, bob_a = await client_of("Bob")
            await holding(bob_a, eq, 6000.0)
            await holding(bob_a, bnd, 4000.0)
            check("on its model, nothing to do", await propose(bob_m), [])
            await write(bob_m, targets={"equity": 0.4, "fixed_income": 0.6}, drift_band=0.03, min_cash=0.05)
            d = (await propose(bob_m))[0]
            check("against the IPS it is 20 points out", (d.payload["target_weights"], d.payload["summary"]["max_drift"]),
                  ({"equity": 0.4, "fixed_income": 0.6}, 0.2))
            check("to the IPS's band", d.payload["summary"]["drift_band"], 0.03)
            check("the proposal carries the version", d.payload["ips"]["version"], 1)
            check("the model still names what to buy", [o["symbol"] for o in d.payload["order_set"] if o["side"] == "buy"], ["BND"])
            check("the rationale says what it was measured against", "IPS (version 1)" in d.rationale, True)
            check("the title is unchanged, so it stays one proposal", d.title.startswith("Rebalance Bob mandate: max drift"), True)
            sold = sum(o["est_value"] for o in d.payload["order_set"] if o["side"] == "sell")
            bought = sum(o["est_value"] for o in d.payload["order_set"] if o["side"] == "buy")
            check("the cash floor is honoured", sold - bought >= 500.0, True)

            cal_h, cal_m, cal_a = await client_of("Cal")
            await holding(cal_a, eq, 7000.0)
            await holding(cal_a, bnd, 3000.0)
            d = (await propose(cal_m))[0]
            check("with no IPS it is still the model", (d.payload["ips"], d.payload["summary"]["drift_band"]), (None, 0.05))
            check("and the rationale says so", "no IPS is on file" in d.rationale, True)
            await write(cal_m, drift_band=0.15)
            check("a wider band in the IPS means no trade", await propose(cal_m), [])

            dee_h, dee_m, dee_a = await client_of("Dee")
            await holding(dee_a, eq, 9000.0)
            await write(dee_m, restricted=["BND"])
            agent = drift.DriftRebalancingAgent()
            sensed = await agent.sense(ctx_for(dee_m))
            check("a model pick the IPS rules out is not offered", [p["symbol"] for p in sensed["model_picks"]], ["EQ"])
            con_h, con_m, con_a = await client_of("Con", constraints={"values_exclusions": ["EQ"]})
            await holding(con_a, eq, 9000.0)
            await holding(con_a, bnd, 1000.0)
            sensed = await agent.sense(ctx_for(con_m))
            check("an exclusion onboarding wrote to constraints is honoured", [p["symbol"] for p in sensed["positions"] if p["excluded"]], ["EQ"])

            print("\n=== Approving after the terms have changed ===")
            order = [{"side": "sell", "instrument_id": str(eq.id), "symbol": "EQ", "quantity": 10, "est_price": 100.0,
                      "est_value": 1000.0, "account_id": str(bob_a.id), "custodian": "X", "est_realised_gain": 0, "reason": "Trim"}]
            then = ips.stamp(await ips.in_force(s, bob_m.id))
            await write(bob_m, targets={"equity": 0.45, "fixed_income": 0.55}, drift_band=0.03, min_cash=0.05, reason="Client asked")
            check("the stamp no longer matches", (await ips.moved_on(s, bob_m, then)).version, 2)
            out = await agent.act(ctx_for(bob_m), SimpleNamespace(id=uuid.uuid4(), payload={"order_set": order, "ips": then}, modified_payload=None))
            check("nothing is placed", (out["executed"], "IPS version 2" in out["note"]), (False, True))
            out = await agent.act(ctx_for(bob_m), SimpleNamespace(id=uuid.uuid4(), payload={"order_set": order}, modified_payload=None))
            check("a proposal from before the IPS is refused too", out["executed"], False)
            now = ips.stamp(await ips.in_force(s, bob_m.id))
            out = await agent.act(ctx_for(bob_m), SimpleNamespace(id=uuid.uuid4(), payload={"order_set": order, "ips": then},
                                                                  modified_payload={"order_set": order, "ips": now}))
            check("a modification cannot carry a newer stamp in", out["executed"], False)
            check("the mandate with no IPS passes this check", await ips.moved_on(s, bare_m, None), None)

            print("\n=== The capacity ceiling reads the IPS ===")
            async def answers(h, capacity, indicated):
                s.add(RiskAssessment(firm_id=firm.id, household_id=h.id, answers={}, scoring_version=1, tolerance=indicated,
                                     capacity=capacity, indicated=indicated, completed_by="c@t", completed_by_role="client",
                                     completed_at=utcnow()))
                await s.flush()

            eve_h, eve_m, eve_a = await client_of("Eve", the_model=cautious)
            await holding(eve_a, eq, 5000.0)
            await holding(eve_a, bnd, 5000.0)
            await write(eve_m, targets={"equity": 0.5, "fixed_income": 0.5})
            await answers(eve_h, "low", "conservative")
            await refused("a model within the ceiling does not excuse an IPS above it",
                          rp.decide(s, firm.id, eve_m.id, decision="adopt", reason=None, by="sophie@t"), "IPS version 1")
            check("so the mandate keeps its capacity", eve_m.suitability["capacity_for_loss"], "medium")
            fay_h, fay_m, fay_a = await client_of("Fay")
            await holding(fay_a, eq, 3500.0)
            await holding(fay_a, bnd, 6500.0)
            await write(fay_m, targets={"equity": 0.35, "fixed_income": 0.65})
            await answers(fay_h, "low", "conservative")
            out = await rp.decide(s, firm.id, fay_m.id, decision="adopt", reason=None, by="sophie@t")
            check("an IPS within it is what counts, whatever the model holds", out["capacity_for_loss"], "low")
            v = await seen(fay_m)
            check("and the IPS now reads out of date", [f["kind"] for f in v["findings"]], ["profile"])
            check("showing what changed", (v["findings"][0]["was"]["capacity_for_loss"], v["findings"][0]["now"]["capacity_for_loss"]), ("medium", "low"))

            print("\n=== Book analytics agree ===")
            nia_h, nia_m, nia_a = await client_of("Nia")
            await holding(nia_a, eq, 7000.0)
            await holding(nia_a, bnd, 3000.0)
            rows = {r["mandate"]: r for r in (await _drift_summary(s, firm.id))["by_mandate"]}
            check("a mandate with an IPS is measured against it", (rows["Bob mandate"]["measured_against"], rows["Bob mandate"]["drift_band"]), ("ips", 0.03))
            check("one without is measured against its model", (rows["Nia mandate"]["measured_against"], rows["Nia mandate"]["drift_band"],
                                                                rows["Nia mandate"]["needs_rebalance"]), ("model", 0.05, True))
            check("a wide IPS band is not a rebalance", rows["Cal mandate"]["needs_rebalance"], False)

            print("\n=== The book, and firm scope ===")
            book = await ips.for_firm(s, firm.id)
            order_seen = [r["status"] for r in book["mandates"]]
            check("breaches come first", order_seen[0], "breach")
            check("no IPS comes last", order_seen[-1], "no_ips")
            check("the summary adds up", sum(book["summary"][k] for k in ("breach", "excepted", "within", "not_assessed", "no_ips")), book["summary"]["mandates"])
            check("an inactive mandate is left out", any(r["mandate"] == "Gone mandate" for r in book["mandates"]), False)
            check("another firm's are not in it", any(r["mandate_id"] == str(ann_m.id) for r in (await ips.for_firm(s, other.id))["mandates"]), False)
            check("nor can one be read by id", await ips.for_mandate(s, ann_m.id, firm_id=other.id), None)
            check("nor a household", await ips.for_household(s, ann.id, firm_id=other.id), None)
            one = await ips.for_household(s, ann.id, firm_id=firm.id)
            check("a household reads its own mandates", [r["mandate"] for r in one["mandates"]], ["Ann mandate"])

            print("\n=== The IPS monitor, through the runtime ===")
            monitor = IpsMonitoringAgent()
            gus_h, gus_m, gus_a = await client_of("Gus", f=quiet, the_model=q_model)
            gus_eq = await holding(gus_a, q_eq, 6000.0)
            gus_bnd = await holding(gus_a, q_bnd, 4000.0)
            hal_h, hal_m, hal_a = await client_of("Hal", f=quiet, the_model=q_model)
            await holding(hal_a, q_eq, 9000.0)
            await holding(hal_a, q_bnd, 1000.0)
            await write(gus_m, f=quiet, max_position=0.55)

            async def sweep():
                await runtime.run_agent(s, firm=quiet, agent_key=KEY, subject=Subject("firm", quiet.id, quiet.name),
                                        trigger="scheduled_monitor")
                await s.flush()

            async def proposals(status=RecommendationStatus.PROPOSED):
                return (await s.execute(select(Recommendation).where(
                    Recommendation.firm_id == quiet.id, Recommendation.agent_key == KEY,
                    Recommendation.status == status))).scalars().all()

            await sweep()
            raised = await proposals()
            check("one proposal for the mandate in breach, none for the one with no IPS", [r.subject_id for r in raised], [gus_m.id])
            check("with figures in its summary", "EQ 60.0% against a limit of 55.0%" in raised[0].summary, True)
            check("saying what approving does", "nothing trades" in raised[0].rationale, True)
            check("carrying the finding", [f["term"] for f in raised[0].payload["findings"]], ["position:EQ"])
            first_id = raised[0].id
            await sweep()
            raised = await proposals()
            check("a second run refreshes it", ([r.id for r in raised], raised[0].payload["refreshed"]["times"]), ([first_id], 2))

            gus_eq.market_value = 5000
            gus_bnd.market_value = 5000
            await sweep()
            check("a breach that clears closes its proposal", await proposals(), [])
            check("as expired, not decided", [r.id for r in await proposals(RecommendationStatus.EXPIRED)], [first_id])

            gus_eq.market_value = 6000
            gus_bnd.market_value = 4000
            await sweep()
            rec = (await proposals())[0]

            print("\n=== Deciding a breach ===")
            c = AgentContext(session=s, firm=quiet, run=None, tier=AutonomyTier.TIER_3, guardrails={}, config={},
                             subject=Subject("mandate", gus_m.id, gus_m.name))
            out = await monitor.act(c, SimpleNamespace(id=rec.id, decided_by=None, payload=rec.payload, modified_payload=None, decision_note=None))
            check("at Tier 3 with nobody deciding, nothing is accepted", (out["executed"], (await seen(gus_m, quiet))["status"]), (False, "breach"))
            out = await monitor.act(c, SimpleNamespace(id=rec.id, decided_by=quiet_adviser.id, payload=rec.payload, modified_payload=None, decision_note="  "))
            check("with no reason, nothing is accepted", (out["executed"], "needs the reason" in out["note"], (await seen(gus_m, quiet))["status"]), (False, True, "breach"))
            chat = ToolExecutors(s, quiet_adviser.id, quiet_adviser.role, quiet.id)
            decide = {"recommendation_id": str(rec.id), "action": "approve"}
            await refused("in the conversation, no reason means no card", chat.preview_change("decide_recommendation", decide), "needs the reason")
            shown = {r["label"]: r["value"] for r in (await chat.preview_change("decide_recommendation", {**decide, "note": "Waiting on year end"}))["rows"]}
            check("the card names what is set aside", ("EQ 60.0%" in shown["Breaches"], shown["Decision"].startswith("Accept until"), shown["Reason"]),
                  (True, True, "Waiting on year end"))
            await runtime.decide(s, firm=quiet, recommendation=rec, action=HumanAction.APPROVE, actor_id=quiet_adviser.id,
                                 actor_label="quinn@t", note="Waiting on the tax year end")
            await s.flush()
            check("an approval accepts it", (rec.status, (await seen(gus_m, quiet))["status"]), (RecommendationStatus.EXECUTED, "excepted"))
            shown = (await seen(gus_m, quiet))["excepted"][0]
            check("under the approver's name, with their reason", (shown["accepted_by"], shown["reason"]), ("quinn@t", "Waiting on the tax year end"))
            check("the book did not move", float(gus_eq.market_value), 6000.0)
            await sweep()
            check("an accepted breach is not raised again", await proposals(), [])
            gus_eq.market_value = 8000
            await sweep()
            check("one that grows is", len(await proposals()), 1)
            gus_eq.market_value = 6000
            await sweep()
            await runtime.rollback(s, firm=quiet, recommendation=rec, actor_id=quiet_adviser.id, actor_label="quinn@t", note="Changed my mind")
            await s.flush()
            check("rolling back withdraws it", (await seen(gus_m, quiet))["status"], "breach")
            kept = (await s.execute(select(IpsException).where(IpsException.mandate_id == gus_m.id))).scalars().all()
            check("and it stays on record as withdrawn", [(r.term, r.withdrawn_by) for r in kept], [("position:EQ", "rollback")])

            print("\n=== A rebalance already answers drift ===")
            iva_h, iva_m, iva_a = await client_of("Iva")
            await holding(iva_a, eq, 8000.0)
            await holding(iva_a, bnd, 2000.0)
            await write(iva_m)
            monitor_ctx = AgentContext(session=s, firm=firm, run=None, tier=AutonomyTier.TIER_2, guardrails={}, config={},
                                       subject=Subject("firm", firm.id, firm.name))
            found = {r["mandate"]: r for r in (await monitor.sense(monitor_ctx))["rows"]}
            check("drift with no rebalance open is raised", [f["kind"] for f in found["Iva mandate"]["findings"]], ["allocation", "allocation"])
            s.add(Recommendation(firm_id=firm.id, run_id=uuid.uuid4(), agent_key=AgentKey.DRIFT_REBALANCING, tier=AutonomyTier.TIER_2,
                                 status=RecommendationStatus.PROPOSED, title="Rebalance Iva", summary="x", rationale="x",
                                 confidence=0.9, priority=2, subject_type="mandate", subject_id=iva_m.id, subject_label="Iva",
                                 payload={}, evidence={}, citations=[]))
            await s.flush()
            found = {r["mandate"]: r for r in (await monitor.sense(monitor_ctx))["rows"]}
            check("while one is open it is left to that proposal", "Iva mandate" in found, False)
            check("the page still shows it", (await seen(iva_m))["status"], "breach")

            print("\n=== Carried from onboarding ===")
            jay_h, jay_m, _ = await client_of("Jay", constraints={"values_exclusions": ["tobacco"]})
            case = OnboardingCase(firm_id=firm.id, prospect_name="Jay", ips_accepted_at=utcnow() - timedelta(days=3),
                                  ips_accepted_by="sophie@t", materialized={"mandate_id": str(jay_m.id)})
            kay_h, kay_m, _ = await client_of("Kay")
            unaccepted = OnboardingCase(firm_id=firm.id, prospect_name="Kay", materialized={"mandate_id": str(kay_m.id)})
            nowhere = OnboardingCase(firm_id=firm.id, prospect_name="Nowhere", ips_accepted_at=utcnow(), materialized={})
            s.add_all([case, unaccepted, nowhere])
            await s.flush()
            check("an accepted, activated case is carried", await ips_backfill.apply(s), 1)
            policy = await ips.in_force(s, jay_m.id)
            check("as what was accepted, by whom, and from where", (policy.source, policy.recorded_by, policy.targets, policy.restricted),
                  ("onboarding", "sophie@t", {"equity": 0.6, "fixed_income": 0.4}, ["tobacco"]))
            check("dated when it was accepted", policy.accepted_on, firm_time.today(firm, case.ips_accepted_at))
            check("one nobody accepted is not", await ips.in_force(s, kay_m.id), None)
            check("running it again changes nothing", await ips_backfill.apply(s), 0)
            check("nothing is invented for the rest of the book", (await seen(ann_m))["ips"]["source"], "recorded")

            print("\n=== Who may do what ===")
            await api.ReaderDep.dependency(priya)
            await api.ReaderDep.dependency(david)
            await forbidden("a client is refused", api.ReaderDep.dependency(client))
            await forbidden("research is refused", api.ReaderDep.dependency(research))
            body = api.RecordIn(accepted_on=today, document="Signed IPS", drift_band=0.04, reason="Reviewed")
            await forbidden("a paraplanner cannot record one", api.record(kay_m.id, body, priya, firm, s))
            await forbidden("nor can compliance", api.record(kay_m.id, body, david, firm, s))
            await forbidden("another firm's mandate is not found", api.record(kay_m.id, body, outsider, other, s), 404)
            await forbidden("nor read", api.read_mandate(kay_m.id, outsider, other, s), 404)
            await forbidden("a refusal is a conflict", api.record(kay_m.id, api.RecordIn(accepted_on=today), sophie, firm, s), 409)
            check("the rights go to the page", ((await api.read(priya, firm, s))["can_record"], (await api.read(sophie, firm, s))["can_record"]), (False, True))
            out = await api.record(kay_m.id, api.RecordIn(accepted_on=today, document="Signed IPS"), sophie, firm, s)
            check("an adviser records it", (out["version"], out["ips"]["recorded_by"]), (1, "sophie@t"))

            print("\n=== In the conversation ===")
            ex = ToolExecutors(s, sophie.id, sophie.role, firm.id)
            lea_h, lea_m, lea_a = await client_of("Lea")
            await holding(lea_a, eq, 6000.0)
            await holding(lea_a, bnd, 4000.0)
            args = {"mandate_id": str(lea_m.id), "accepted_on": today.isoformat(), "document": "Signed IPS",
                    "targets": ["equity 55", "fixed_income: 45%"], "drift_band": 10, "max_position": 70, "min_cash": 0,
                    "restricted": ["BTI"]}
            shown = {r["label"]: r["value"] for r in (await ex.preview_change("record_ips", args))["rows"]}
            check("the card names the mandate and version", (shown["Mandate"], shown["Version"]), ("Lea mandate", "1"))
            check("every term, as a percentage", (shown["Targets"], shown["Band"], shown["Largest holding"], shown["Cash floor"], shown["Restricted"]),
                  ("equity 55.0%, fixed income 45.0%", "±10.0%", "70.0%", "0.0%", "BTI"))
            check("a preview writes nothing", await ips.in_force(s, lea_m.id), None)
            await refused("the card refuses what recording would", ex.preview_change("record_ips", {**args, "targets": ["equity 55"]}), "add up to 100%")
            await refused("a target it cannot read", ex.preview_change("record_ips", {**args, "targets": ["equity"]}), "like 'equity 60'")
            await refused("a date it cannot read", ex.preview_change("record_ips", {**args, "accepted_on": "last week"}), "YYYY-MM-DD")
            await refused("a role that slips past is refused by the executor",
                          ToolExecutors(s, priya.id, priya.role, firm.id).record_ips(args), "only an adviser")
            out = await ex.record_ips(args)
            check("confirming records it", (out["version"], out["targets"], out["drift_band"], out["ips"]["min_cash"]),
                  (1, {"equity": 0.55, "fixed_income": 0.45}, 0.1, 0.0))
            check("and says where it stands", "version 1" in out["message"], True)
            check("reading the book", "in breach of their IPS" in (await ex.read_ips_monitoring({}))["message"], True)
            check("reading a household", (await ex.read_ips_monitoring({"household_id": str(lea_h.id)}))["summary"]["within"], 1)
            check("reading a mandate", (await ex.read_ips_monitoring({"mandate_id": str(lea_m.id)}))["ips"]["version"], 1)
            await refused("another firm cannot read it", ToolExecutors(s, outsider.id, outsider.role, other.id).read_ips_monitoring(
                {"mandate_id": str(lea_m.id)}), "not found")
            mid = await ex.read_ips_monitoring({"mandate_id": str(bare_m.id)})
            check("no IPS is said as no record here", "no IPS recorded here" in mid["message"], True)
            check("Astra opens it beside the conversation", (await ex.open_view({"view": "ips"}))["view"], "/studio/ips")
            check("for compliance too", (await ToolExecutors(s, david.id, david.role, firm.id).open_view({"view": "ips"}))["view"], "/studio/ips")
            await refused("not for a role that does not read it", ToolExecutors(s, research.id, research.role, firm.id).open_view({"view": "ips"}), "not one this role opens")

            print("\n=== The drafter reads the assessment and the terms ===")
            mia_h, mia_m, mia_a = await client_of("Mia", exclude=["BTI"])
            await holding(mia_a, eq, 6000.0)
            await holding(mia_a, bnd, 4000.0)
            drafter = ips_drafting.IPSDraftingAgent()
            dc = AgentContext(session=s, firm=firm, run=None, tier=AutonomyTier.TIER_2, guardrails={}, config={},
                              subject=Subject("household", mia_h.id, mia_h.name))
            draft = (await drafter.think(dc, await drafter.sense(dc)))[0]
            check("no questionnaire is said", draft.payload["rrttllu"]["assessment"], "No scored questionnaire on file")
            check("the model's targets are given to it", "equity 60%, fixed income 40%; band ±5.0%; from the 60/40 model" in draft.payload["rrttllu"]["policy"], True)
            check("an exclusion on the suitability record reaches it", draft.payload["rrttllu"]["unique_preferences"], "BTI")
            await answers(mia_h, "low", "conservative")
            await write(mia_m, targets={"equity": 0.4, "fixed_income": 0.6})
            draft = (await drafter.think(dc, await drafter.sense(dc)))[0]
            check("the scored answers reach it", draft.payload["rrttllu"]["assessment"],
                  "Questionnaire: tolerance conservative, capacity low, indicated conservative")
            check("and the IPS in force", "from IPS version 1 in force" in draft.payload["rrttllu"]["policy"], True)
            check("it is never monitored against", (await ips.in_force(s, mia_m.id)).version, 1)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
