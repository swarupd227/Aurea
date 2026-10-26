"""Prove the billing run lifecycle (L200-5): a run is proposed, issued once on approval,
takes the fee from the mandate's own cash and no further, holds what is blocked, lets an
approver knowingly accept only a stale valuation, and is undone by offset - never by edit.

Also the controls around it: who may approve a run, and that the agent proposes rather than
bills. Every expected figure is worked by hand beside it. Runs against a throwaway SQLite
database so it needs no cloud connection.

    python -m tests.verify_billing_run
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.agents.billing import BillingAgent
from app.atlas.base import AgentContext, Subject
from app.aurea_core import billing, billing_run
from app.core import decision_rights as dr
from app.core.db import Base
from app.models.billing import BillingRun, Invoice
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import FeeSchedule
from app.models.portfolio import Holding, Instrument, Price, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = datetime.now(timezone.utc).date()
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def check(label: str, got, want, tol: float = 0.005) -> None:
    ok = (abs(float(got) - float(want)) < tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def run() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "household", "person", "legal_entity", "mandate", "account", "instrument", "price",
              "holding", "fee_schedule", "fee_arrangement", "billing_run", "invoice", "txn", "agent_run",
              "recommendation", "app_user"]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[Base.metadata.tables[n] for n in wanted]))
    Session = async_sessionmaker(db, expire_on_commit=False)

    try:
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Test", slug="test", base_currency="NZD")
            s.add(firm)
            await s.flush()
            tiered = FeeSchedule(id=uuid.uuid4(), firm_id=firm.id, code="T", name="Tiered", fee_type="tiered_bps",
                                 tiers=[{"min_aum": 0, "max_aum": 1_000_000, "bps": 100},
                                        {"min_aum": 1_000_000, "max_aum": None, "bps": 75}],
                                 minimum_annual_fee=Decimal("3000"), currency="NZD")
            s.add(tiered)
            approver = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@test", full_name="Ops",
                            hashed_password="x", role=UserRole.OPERATIONS)
            s.add(approver)
            await s.flush()

            async def instrument(sym, days_old):
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class="equity")
                s.add(inst)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=TODAY - timedelta(days=days_old),
                            close=10, currency="NZD", source="test", is_real=True))
                await s.flush()
                return inst

            fresh, old = await instrument("FRESH", 3), await instrument("OLD", 60)

            async def mandate(name, *, holdings, cash, inst, currency="NZD", confirmed=True):
                hh = Household(id=uuid.uuid4(), firm_id=firm.id, name=f"{name} household")
                s.add(hh)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name=name)
                s.add(p)
                await s.flush()
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=name,
                            mandate_type="advisory", is_active=True)
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} account",
                            currency=currency, cash_balance=Decimal(str(cash)))
                s.add(a)
                await s.flush()
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=inst.id,
                              quantity=Decimal("1"), market_value=Decimal(str(holdings)), cost_basis=Decimal("0")))
                await billing.activate_arrangement(
                    s, firm_id=firm.id, mandate=m, fee_schedule_id=tiered.id, billing_method="arrears",
                    billing_frequency="quarterly", householding=False, effective_from=date(2020, 1, 1),
                    set_by="maker@x", set_at=NOW, confirmed_by="checker@x" if confirmed else None,
                    confirmed_at=NOW if confirmed else None, source="seed")
                await s.flush()
                return m, a

            # Each is 1,000,000 of value: 10,000 a year, 2,500 a quarter.
            m1, a1 = await mandate("M1 Funded", holdings=900_000, cash=100_000, inst=fresh)
            m2, a2 = await mandate("M2 NoCash", holdings=1_000_000, cash=0, inst=fresh)
            m3, a3 = await mandate("M3 Stale", holdings=995_000, cash=5_000, inst=old)
            m4, a4 = await mandate("M4 Usd", holdings=1_000_000, cash=0, inst=fresh, currency="USD")
            await mandate("M5 Unconfirmed", holdings=500_000, cash=0, inst=fresh, confirmed=False)

            async def invoice_of(run, name):
                return (await s.execute(select(Invoice).where(
                    Invoice.run_id == run.id, Invoice.mandate_name == name,
                    Invoice.kind == "invoice"))).scalars().first()

            async def cash(account):
                await s.refresh(account)
                return float(account.cash_balance)

            def agent_ctx():
                ar = AgentRun(firm_id=firm.id, agent_key="billing", status="sensing", tier="tier_2",
                              trigger="manual", started_at=datetime.now(timezone.utc))
                s.add(ar)
                return ar

            print("\n=== The agent proposes; it does not bill ===")
            ar = agent_ctx()
            await s.flush()
            ctx = AgentContext(session=s, firm=firm, run=ar, tier=AutonomyTier.TIER_2, guardrails={},
                               config={}, subject=Subject("firm", firm.id, firm.name))
            agent = BillingAgent()
            sensed = await agent.sense(ctx)
            run1 = await s.get(BillingRun, uuid.UUID(sensed["run_id"]))
            check("the run waits for approval", run1.status, "proposed")
            check("the unconfirmed arrangement is not in it",
                  await invoice_of(run1, "M5 Unconfirmed"), None)
            drafts = await agent.think(ctx, sensed)
            check("one proposal is drafted", len(drafts), 1)
            check("it covers four bills", drafts[0].payload["invoices"], 4)
            check("two are held by a block", drafts[0].payload["held"], 2)
            check("the billable total is 5,000", drafts[0].payload["amount_by_currency"]["NZD"], 5_000)
            check("the mandate with no agreement is named", drafts[0].payload["no_agreement"], ["M5 Unconfirmed"])
            check("nothing is issued yet", (await invoice_of(run1, "M1 Funded")).status, "draft")
            check("and no cash has moved", await cash(a1), 100_000)
            sensed_again = await agent.sense(ctx)
            check("a second run is not stacked behind the first", sensed_again, {"waiting_run_id": str(run1.id)})
            check("and nothing is proposed while one waits", await agent.think(ctx, sensed_again), [])
            try:
                await billing_run.propose_run(s, firm.id)
                check("a duplicate proposal is refused", "accepted", "refused")
            except billing_run.BillingError:
                check("a duplicate proposal is refused", "refused", "refused")

            rec = Recommendation(
                firm_id=firm.id, run_id=ar.id, agent_key="billing", tier="tier_2",
                status=RecommendationStatus.PROPOSED, title=drafts[0].title, summary=drafts[0].summary,
                payload=drafts[0].payload, decided_by=approver.id)
            s.add(rec)
            await s.flush()

            print("\n=== Approving issues the bills and takes the fee from cash, and no further ===")
            result = await agent.act(ctx, rec)
            check("two bills issued", result["issued"], 2)
            check("two held", result["held"], 2)
            check("one paid from cash", result["debited"], 1)
            check("one issued with not enough cash", result["insufficient_cash"], 1)
            i1, i2 = await invoice_of(run1, "M1 Funded"), await invoice_of(run1, "M2 NoCash")
            check("M1 is debited", i1.status, "debited")
            check("2,500 left M1's cash", await cash(a1), 97_500)
            fee = (await s.execute(select(Transaction).where(Transaction.account_id == a1.id,
                                                             Transaction.txn_type == "fee"))).scalars().all()
            check("the fee is its own ledger entry", (len(fee), float(fee[0].amount)), (1, 2_500.0))
            check("it points back at the invoice", fee[0].lineage["invoice_id"], str(i1.id))
            check("M2 is issued but unpaid", (i2.status, i2.debit_status), ("issued", "insufficient_cash"))
            check("the shortfall is recorded", i2.debit_detail["shortfall"], 2_500)
            check("an empty account is never overdrawn", await cash(a2), 0)
            check("bills are numbered in order", (i1.invoice_number[-4:], i2.invoice_number[-4:]), ("0001", "0002"))
            check("the run is issued by whoever approved it", (run1.status, run1.approved_by), ("issued", "ops@test"))
            check("the recommendation is linked back", run1.recommendation_id, rec.id)
            m3_inv, m4_inv = await invoice_of(run1, "M3 Stale"), await invoice_of(run1, "M4 Usd")
            check("blocked bills are held, with no number", (m3_inv.status, m3_inv.invoice_number), ("held", None))
            check("a currency we cannot convert is held", m4_inv.status, "held")
            check("the money in the run matches what was issued", result["issued_by_currency"]["NZD"], 5_000)

            print("\n=== A run is issued once ===")
            try:
                await billing_run.approve_run(s, run1, approved_by="again@test")
                check("approving twice is refused", "accepted", "refused")
            except billing_run.BillingError:
                check("approving twice is refused", "refused", "refused")
            check("and nothing was taken twice", await cash(a1), 97_500)

            print("\n=== Billed periods are not billed again; held ones are re-proposed ===")
            ar2 = agent_ctx()
            await s.flush()
            run2 = await billing_run.propose_run(s, firm.id, agent_run_id=ar2.id)
            check("only the two held bills come back", run2.summary["invoices"], 2)
            check("the two already billed are counted, not repeated", run2.summary["already_billed"], 2)
            check("the superseded held bills are voided",
                  (await s.get(Invoice, m3_inv.id)).status, "void")

            print("\n=== An approver may accept a stale valuation, and nothing else ===")
            result2 = await billing_run.approve_run(
                s, run2, approved_by="ops@test", acknowledge=["stale_valuation", "currency_mismatch"])
            check("the stale bill is issued", result2["issued"], 1)
            check("the currency bill is still held - it cannot be accepted", result2["held"], 1)
            m3_new = await invoice_of(run2, "M3 Stale")
            check("the acceptance is recorded against the invoice",
                  [(o["code"], o["accepted_by"]) for o in m3_new.overrides], [("stale_valuation", "ops@test")])
            check("it was debited from the cash it had", (m3_new.status, await cash(a3)), ("debited", 2_500))
            check("it kept the numbering going", m3_new.invoice_number[-4:], "0003")

            print("\n=== A correction is a credit note; the bill is kept ===")
            note = await billing_run.credit_invoice(s, i1, reason="Billed in error", by="ops@test")
            check("the credit note is the same amount, negative", float(note.amount), -2_500)
            check("it has its own number", note.invoice_number.startswith("CN-"), True)
            check("the original is kept and marked credited", (await s.get(Invoice, i1.id)).status, "credited")
            check("the cash is returned", await cash(a1), 100_000)
            kinds = (await s.execute(select(Transaction.txn_type).where(Transaction.account_id == a1.id,
                                                                        Transaction.txn_type.in_(("fee", "fee_credit"))))).scalars().all()
            check("the fee and its credit are both on the ledger", sorted(kinds), ["fee", "fee_credit"])
            try:
                await billing_run.credit_invoice(s, i1, reason="again", by="ops@test")
                check("crediting twice is refused", "accepted", "refused")
            except billing_run.BillingError:
                check("crediting twice is refused", "refused", "refused")
            try:
                await billing_run.credit_invoice(s, m4_inv, reason="x", by="ops@test")
                check("a bill that was never issued cannot be credited", "accepted", "refused")
            except billing_run.BillingError:
                check("a bill that was never issued cannot be credited", "refused", "refused")
            outcome = await billing_run.credit_run(s, run1, reason="Run rolled back", by="ops@test")
            check("rolling back the run offsets what is left of it", outcome["credit_notes"], 1)
            check("the unpaid bill is credited without touching cash", await cash(a2), 0)
            check("the run is marked credited", run1.status, "credited")
            check("the agent's own rollback reports nothing left to offset",
                  (await agent.rollback(ctx, rec))["reversed"], False)

            print("\n=== A dismissed proposal does not block the next run ===")
            ar3 = agent_ctx()
            await s.flush()
            run3 = await billing_run.propose_run(s, firm.id, agent_run_id=ar3.id)
            # M1 and M2 were credited above, so their periods are billable again - a correction is
            # credit then rebill - and M4 is still held. M3 is paid and stays billed.
            check("credited and held bills come round again, a paid one does not", run3.summary["invoices"], 3)
            s.add(Recommendation(firm_id=firm.id, run_id=ar3.id, agent_key="billing", tier="tier_2",
                                 status=RecommendationStatus.DISMISSED, title="x", summary="x"))
            await s.flush()
            check("a dismissed run is no longer open", await billing_run.open_run(s, firm.id), None)
            check("it is cancelled", (await s.get(BillingRun, run3.id)).status, "cancelled")
            check("and its drafts are voided", (await invoice_of(run3, "M4 Usd")).status, "void")

            print("\n=== Another firm sees none of it ===")
            other = await billing.preview(s, uuid.uuid4(), as_of=TODAY)
            check("no arrangements leak across firms", len(other["arrangements"]), 0)
            check("no open run leaks either", await billing_run.open_run(s, uuid.uuid4()), None)
            n = (await s.execute(select(func.count()).select_from(Invoice).where(Invoice.firm_id == uuid.uuid4()))).scalar()
            check("no invoices either", n, 0)

        print("\n=== Who may approve a billing run ===")
        b = AgentKey.BILLING
        check("operations and a branch leader approve",
              dr.roles_for(b, "approve"), frozenset({UserRole.OPERATIONS, UserRole.BRANCH_LEADER}))
        check("modifying - the only way to accept a stale valuation - is the same group",
              dr.roles_for(b, "modify"), dr.roles_for(b, "approve"))
        check("compliance may dismiss", UserRole.COMPLIANCE in dr.roles_for(b, "dismiss"), True)
        check("compliance may roll back", UserRole.COMPLIANCE in dr.roles_for(b, "rollback"), True)
        check("compliance may not approve", UserRole.COMPLIANCE in dr.roles_for(b, "approve"), False)
        for role in (UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN, UserRole.RESEARCH_CIO, UserRole.CLIENT):
            check(f"{role.value} may not approve fees", role in dr.roles_for(b, "approve"), False)
        check("an administrator holds neither power", UserRole.ADMIN in dr.roles_for(b, "dismiss"), False)
        try:
            dr.check(UserRole.ADVISER, b, "approve")
            check("an adviser is refused with a reason", "allowed", "refused")
        except dr.DecisionForbidden as exc:
            check("an adviser is refused with a reason", "operations" in str(exc), True)
        check("billing moves client cash, not the book", (dr.moves_client_cash(b), dr.moves_the_book(b)), (True, False))
        check("trade approval is unchanged: an adviser still approves a rebalance",
              UserRole.ADVISER in dr.roles_for(AgentKey.DRIFT_REBALANCING, "approve"), True)
        check("and operations still may not", UserRole.OPERATIONS in dr.roles_for(AgentKey.DRIFT_REBALANCING, "approve"), False)
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit


def main() -> int:
    asyncio.run(run())
    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
