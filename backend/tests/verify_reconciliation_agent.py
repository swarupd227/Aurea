"""Prove the reconciliation agent: it re-checks statements, leaves timing to clear, puts the rest to
a person in one proposal, and records decisions on breaks only when operations or the portfolio team
approves. It never changes the book and never escalates on its own.

Runs the real agent through the real runtime against a throwaway SQLite database.

    python -m tests.verify_reconciliation_agent
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import schedules
from app.agents.catalogue import CATALOGUE
from app.agents.reconciliation import ReconciliationAgent
from app.atlas import runtime
from app.atlas.base import AgentContext, Subject
from app.aurea_core import reconciliation as rc
from app.core import agent_runs, decision_rights
from app.core.db import Base
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.enums import (AgentKey, AutonomyTier, HumanAction, MarketType, RecommendationStatus,
                              UserRole)
from app.models.governance import Recommendation
from app.models.graph import Account
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Transaction
from app.models.reconciliation import ReconBreak
from app.models.tenant import Firm

PASS, FAIL = [], []
KEY = AgentKey.RECONCILIATION
P = RecommendationStatus.PROPOSED


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def rules() -> None:
    print("\n=== Who runs it, who decides, when it runs ===")
    check("operations and the portfolio team may start it", sorted(r.value for r in agent_runs.run_roles(KEY)),
          ["operations", "portfolio_team"])
    check("an adviser may not", agent_runs.may_run(UserRole.ADVISER, KEY), False)
    check("a run from a sentence stops at a proposal", agent_runs.forced_to_proposal(KEY), True)
    check("even at Tier 3 it does not act on its own", agent_runs.acts_on_its_own(KEY, "tier_3"), False)
    check("operations and the portfolio team decide its proposals",
          sorted(r.value for r in decision_rights.roles_for(KEY, "approve")), ["operations", "portfolio_team"])
    for role in (UserRole.ADVISER, UserRole.COMPLIANCE, UserRole.ADMIN):
        try:
            decision_rights.check(role, KEY, "approve")
            check(f"{role.value} may not approve", False, True)
        except decision_rights.DecisionForbidden as e:
            check(f"{role.value} may not approve", "operations or the portfolio team" in str(e), True)
    check("compliance is not chased for it", decision_rights.queue_action(UserRole.COMPLIANCE, KEY), None)
    check("operations is asked to decide", decision_rights.queue_action(UserRole.OPERATIONS, KEY), "decide")
    check("it runs firm-wide", CATALOGUE[KEY]["subject"], "firm")
    check("on business-day mornings, after the price run", schedules.default_for(KEY)[0], "30 6 * * 1-5")


async def main() -> int:
    rules()
    agent = ReconciliationAgent()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date.today()
        stmt_day = today - timedelta(days=1)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            s.add(firm)
            await s.flush()
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@x", full_name="Ops", role=UserRole.OPERATIONS,
                       hashed_password="x")
            s.add(ops)
            aaa, bbb = (Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class="equity",
                                   market_type=MarketType.PUBLIC, currency="NZD") for sym in ("AAA", "BBB"))
            s.add_all([aaa, bbb])
            await s.flush()

            async def account(name, cash=10000, aaa_q=100, bbb_q=50):
                a = Account(id=uuid.uuid4(), firm_id=firm.id, name=name, currency="NZD", cash_balance=cash)
                s.add(a)
                await s.flush()
                s.add_all([Holding(firm_id=firm.id, account_id=a.id, instrument_id=aaa.id, quantity=aaa_q,
                                   market_value=0, cost_basis=0),
                           Holding(firm_id=firm.id, account_id=a.id, instrument_id=bbb.id, quantity=bbb_q,
                                   market_value=0, cost_basis=0)])
                await s.flush()
                return a

            async def statement(a, aaa_q, bbb_q=50, cash=10000, extra=(), as_of=stmt_day):
                return await rc.import_statement(
                    s, firm.id, account_id=a.id, as_of=as_of, cash_balance=cash, source_document="Statement doc",
                    positions=[{"symbol": "AAA", "quantity": aaa_q}, {"symbol": "BBB", "quantity": bbb_q}, *extra],
                    imported_by="ops@x", today=today)

            async def brk(a, kind=None):
                rows = (await s.execute(select(ReconBreak).where(ReconBreak.account_id == a.id))).scalars().all()
                rows = [r for r in rows if kind is None or r.kind == kind]
                return rows[0] if rows else None

            async def run():
                r = await runtime.run_agent(s, firm=firm, agent_key=KEY, subject=Subject("firm", firm.id, firm.name),
                                            trigger="schedule")
                await s.commit()
                return r

            async def proposals(status=P):
                return (await s.execute(select(Recommendation).where(
                    Recommendation.agent_key == KEY.value, Recommendation.status == status))).scalars().all()

            # unexplained: the custodian holds 10 fewer and nothing on the book says why
            u = await account("Unexplained")
            await statement(u, 90)
            # timing: a buy two days before the statement the custodian has not settled
            t = await account("Timing", cash=8995, aaa_q=110)
            s.add(Transaction(firm_id=firm.id, account_id=t.id, instrument_id=aaa.id, txn_type="buy", quantity=10,
                              price=100, amount=1000, trade_date=stmt_day - timedelta(days=2), lineage={"fees": 5}))
            await s.flush()
            await statement(t, 100, cash=10000)
            # a dividend the custodian has paid that the book has not posted
            d = await account("Dividend")
            div = CorporateAction(firm_id=firm.id, instrument_id=bbb.id, action_type="cash_dividend",
                                  status="announced", payable_date=stmt_day - timedelta(days=1),
                                  details={"cash_per_share": 1.0})
            s.add(div)
            await s.flush()
            ent = CorporateActionEntitlement(firm_id=firm.id, corporate_action_id=div.id, account_id=d.id,
                                             status="pending", shares_entitled=50)
            s.add(ent)
            await s.flush()
            await statement(d, 100, cash=10050)
            # a security the firm has no instrument for
            m = await account("Unmapped")
            await statement(m, 100, extra=[{"symbol": "ZZZ", "quantity": 7}])
            await s.commit()

            print("\n=== A run puts the queue to a person ===")
            await run()
            (rec,) = await proposals()
            items = {i["account"]: i for i in rec.payload["items"]}
            check("one proposal for the queue", rec.title, "Reconciliation - 3 break(s) need a decision")
            check("what nothing explains is to be escalated", (items["Unexplained"]["reason"],
                                                                items["Unexplained"]["action"]),
                  ("unexplained", "escalated"))
            check("a dividend to post is to be accepted and fixed here",
                  (items["Dividend"]["reason"], items["Dividend"]["action"]), ("entitlement_to_post", "explained"))
            check("an unmapped security is to be accepted and mapped",
                  (items["Unmapped"]["reason"], items["Unmapped"]["action"]), ("unmapped_security", "explained"))
            check("timing inside a settlement cycle is left to clear", "Timing" in items, False)
            check("and counted as waiting", rec.evidence["waiting"], 2)
            check("escalations come first", rec.payload["items"][0]["action"], "escalated")
            check("it waits for a person", rec.status, P)
            check("no break was touched by the agent", sorted({str(b.status) for b in (await s.execute(
                select(ReconBreak))).scalars().all()}), ["open"])
            check("the coverage figures travel with it", rec.evidence["coverage"]["accounts"], 4)

            ctx = AgentContext(session=s, firm=firm, run=None, tier=AutonomyTier.TIER_3, guardrails={}, config={},
                               subject=Subject("firm", firm.id, firm.name))
            out = await agent.act(ctx, rec)
            check("at Tier 3 with nobody deciding, it does nothing", (out["executed"], str((await brk(u)).status)),
                  (False, "open"))

            print("\n=== The next run re-checks, and keeps one proposal ===")
            # The dividend is posted on the book - dated the day it was paid, before the statement.
            d.cash_balance = 10050
            s.add(Transaction(firm_id=firm.id, account_id=d.id, instrument_id=bbb.id, txn_type="dividend",
                              quantity=50, price=1, amount=50, trade_date=stmt_day - timedelta(days=1),
                              lineage={"corporate_action_id": str(div.id)}))
            ent.status = "posted"
            await s.commit()
            await run()
            check("the posted dividend cleared its break without a new statement", str((await brk(d)).status),
                  "cleared")
            (rec2,) = await proposals()
            check("the same proposal is refreshed, not a second one", (rec2.id, len(await proposals())), (rec.id, 1))
            check("now with two breaks", rec2.title, "Reconciliation - 2 break(s) need a decision")
            check("re-checking the same statement is not seeing a break again", (await brk(u)).times_seen, 1)

            print("\n=== Approving records each decision under the approver's name ===")
            await rc.resolve_break(s, firm.id, (await brk(m)).id, action="dismissed", note="Mapped by hand",
                                   by="other@x")
            await s.commit()
            await runtime.decide(s, firm=firm, recommendation=rec2, action=HumanAction.APPROVE, actor_id=ops.id,
                                 actor_label="ops@x")
            await s.commit()
            ub = await brk(u)
            check("the unexplained break is escalated", (str(ub.status), ub.resolved_by), ("escalated", "ops@x"))
            check("with the agent's reason on its record", ub.resolution_note.startswith("Approved from the "
                                                                                        "reconciliation agent"), True)
            mb = await brk(m)
            check("a break decided by hand meanwhile is left as it was", (str(mb.status), mb.resolved_by),
                  ("dismissed", "other@x"))
            await s.refresh(rec2)
            result = rec2.payload["execution_result"]
            check("and the outcome counts what was done and what had moved on",
                  (result["escalated"], result["explained"], len(result["skipped"]), rec2.status),
                  (1, 0, 1, RecommendationStatus.EXECUTED))
            held = (await s.execute(select(Holding).where(Holding.account_id == u.id,
                                                          Holding.instrument_id == aaa.id))).scalar_one()
            check("the book was never changed", float(held.quantity), 100.0)

            print("\n=== A proposal nobody needs any more is closed ===")
            u2 = await account("Second")
            await statement(u2, 80)
            await s.commit()
            await run()
            (rec3,) = await proposals()
            check("a new difference raises a new proposal", rec3.title, "Reconciliation - 1 break(s) need a decision")
            await rc.resolve_break(s, firm.id, (await brk(u2)).id, action="escalated", note=None, by="ops@x")
            await s.commit()
            await run()
            check("once it is decided by hand, the next run closes the proposal", len(await proposals()), 0)
            await s.refresh(rec3)
            check("as expired, with why", (rec3.status, rec3.decision_note),
                  (RecommendationStatus.EXPIRED, "No longer found when the book was last checked."))
            check("a run with nothing to decide raises nothing", len(await proposals()), 0)

            print("\n=== Timing that did not clear ===")
            late = await account("Late", cash=8995, aaa_q=110)
            s.add(Transaction(firm_id=firm.id, account_id=late.id, instrument_id=aaa.id, txn_type="buy", quantity=10,
                              price=100, amount=1000, trade_date=today - timedelta(days=12), lineage={"fees": 5}))
            await s.flush()
            await statement(late, 100, cash=10000, as_of=today - timedelta(days=10))
            check("at first it is timing", (await brk(late, "quantity")).cause, "settlement_timing")
            await statement(late, 100, cash=10000, as_of=stmt_day)
            lb = await brk(late, "quantity")
            check("a statement a week later still showing it is no longer timing", lb.cause, "unexplained")
            check("and the change of cause is on its history", [h["event"] for h in lb.history][-1], "cause_changed")
            await s.commit()
            await run()
            (rec4,) = await proposals()
            check("so it is put to a person to escalate",
                  sorted((i["account"], i["kind"], i["action"]) for i in rec4.payload["items"]),
                  [("Late", "cash", "escalated"), ("Late", "quantity", "escalated")])
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
