"""Prove the two agents the user released on 2 Nov: Tax Intelligence reads each market's own report, and Reverse
Churning measures a review or a meeting, is not reset by its own proposal, and keeps one flag.

Runs against a throwaway SQLite database.

    python -m tests.verify_agent_decisions
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.reverse_churning import ReverseChurningAgent
from app.agents.tax_intelligence import TaxIntelligenceAgent
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, markets
from app.core.db import Base
from app.models.engagement import ClientReview, Meeting, Task
from app.models.enums import MarketType, MeetingStatus
from app.models.governance import SurveillanceFlag
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price, TaxLot
from app.models.tenant import Firm
from seed import retirement_dates_backfill

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def book(s, firm, name, *, jurisdiction, cost, price=100.0, qty=1000.0, days=100, tax=None) -> Household:
    today = firm_time.today(firm)
    h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name, values={"jurisdiction": jurisdiction})
    s.add(h)
    await s.flush()
    p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=name, date_of_birth=date(1960, 1, 1), profile={"tax": tax or {"annual_income": 400000}})
    s.add(p)
    await s.flush()
    m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate", mandate_type="advisory", suitability={}, constraints={})
    s.add(m)
    await s.flush()
    a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} A/C", currency="USD", cash_balance=0)
    i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=name[:6].upper(), name=name, asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
    s.add_all([a, i])
    await s.flush()
    s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=price, currency="USD", source="test", is_real=True))
    held = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=i.id, quantity=qty, market_value=qty * price, cost_basis=qty * cost)
    s.add(held)
    await s.flush()
    s.add(TaxLot(firm_id=firm.id, holding_id=held.id, quantity=qty, cost_per_unit=cost, acquired_on=today - timedelta(days=days)))
    await s.flush()
    return h


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            print("\n=== Tax Intelligence, market by market ===")
            for code, currency, cost in (("NZ", "NZD", 150.0), ("US", "USD", 50.0), ("UK", "GBP", 50.0)):
                firm = Firm(id=uuid.uuid4(), name=code, slug=code.lower(), base_currency=currency, jurisdiction=code)
                s.add(firm)
                await s.flush()
                h = await book(s, firm, f"{code} Family", jurisdiction=code, cost=cost)
                ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("firm", None, ""))
                agent = TaxIntelligenceAgent()
                drafts = await agent.think(ctx, await agent.sense(ctx))
                check(f"{code} raises a memo, and does not raise an error", len(drafts), 1)
                d = drafts[0]
                check(f"{code} is about the right household", (d.title, d.subject.id), (f"Tax intelligence — {code} Family", h.id))
                check(f"{code} says it in its own currency", markets.money(1, currency)[0] in d.summary, True)
                check(f"{code} carries its own evidence", d.evidence["jurisdiction"], code)
                check(f"{code} calls a figure an estimate", "estimate" in d.rationale.lower() or "est." in d.summary, True)
            eu = Firm(id=uuid.uuid4(), name="EU", slug="eu", base_currency="EUR", jurisdiction="EU")
            s.add(eu)
            await s.flush()
            await book(s, eu, "EU Family", jurisdiction="EU", cost=50.0)
            ctx = AgentContext(session=s, firm=eu, run=None, tier=None, guardrails={}, config={}, subject=Subject("firm", None, ""))
            agent = TaxIntelligenceAgent()
            check("a market with no report raises nothing", await agent.think(ctx, await agent.sense(ctx)), [])

            print("\n=== Reverse Churning ===")
            firm = Firm(id=uuid.uuid4(), name="RC", slug="rc", base_currency="USD", jurisdiction="US")
            s.add(firm)
            await s.flush()
            today = firm_time.today(firm)
            ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("firm", None, ""))
            agent = ReverseChurningAgent()
            none = await book(s, firm, "Nothing", jurisdiction="US", cost=90.0)
            s.add(Task(firm_id=firm.id, household_id=none.id, title="An agent's task", source="agent"))
            await s.flush()
            sensed = await agent.sense(ctx)
            check("no record is not measured", (sensed["flagged"], sensed["not_measured"]), ([], 1))
            check("so nothing is raised", await agent.think(ctx, sensed), [])
            stale = await book(s, firm, "Stale", jurisdiction="US", cost=90.0)
            s.add(ClientReview(firm_id=firm.id, household_id=stale.id, held_on=today - timedelta(days=500), covered=["tax_items"], recorded_by="a", recorded_by_role="adviser"))
            s.add(Task(firm_id=firm.id, household_id=stale.id, title="Agent's recent task", source="agent"))
            await s.flush()
            sensed = await agent.sense(ctx)
            check("a review over a year old is measured, and a task does not reset it", [r["brain"]["household"]["name"] for r in sensed["flagged"]], ["Stale"])
            drafts = await agent.think(ctx, sensed)
            check("one proposal", len(drafts), 1)
            check("it says what it measured", "no review or meeting on record" in drafts[0].summary, True)
            flags = select(func.count()).select_from(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm.id)
            check("one flag", (await s.execute(flags)).scalar(), 1)
            await agent.think(ctx, await agent.sense(ctx))
            check("a second run adds no flag", (await s.execute(flags)).scalar(), 1)
            for f in (await s.execute(select(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm.id))).scalars().all():
                f.resolved = True
            await s.flush()
            await agent.think(ctx, await agent.sense(ctx))
            check("once resolved, finding it again raises a new one", (await s.execute(flags)).scalar(), 2)
            s.add(Meeting(firm_id=firm.id, household_id=stale.id, title="Catch-up", scheduled_at=datetime.now(timezone.utc) - timedelta(days=30), status=MeetingStatus.COMPLETED.value))
            await s.flush()
            check("a meeting held clears it", (await agent.sense(ctx))["flagged"], [])
            fresh = await book(s, firm, "Fresh", jurisdiction="US", cost=90.0)
            s.add(ClientReview(firm_id=firm.id, household_id=fresh.id, held_on=today - timedelta(days=30), covered=[], recorded_by="unknown", recorded_by_role="unknown"))
            await s.flush()
            check("a recent review is activity", [r["brain"]["household"]["name"] for r in (await agent.sense(ctx))["flagged"]], [])

            print("\n=== The two seeded retirement dates ===")
            hart = Household(id=uuid.uuid4(), firm_id=firm.id, name="Hartley")
            s.add(hart)
            await s.flush()
            james = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hart.id, full_name="James Hartley", date_of_birth=date(1958, 9, 12))
            s.add(james)
            await s.flush()
            goal = Goal(id=uuid.uuid4(), firm_id=firm.id, household_id=hart.id, person_id=james.id, name="Retirement", kind="retirement", target_amount=5000000,
                        target_date=date(2036, 1, 1), priority=1, assumptions={"years": 10, "funding_share": 0.8})
            s.add(goal)
            await s.flush()
            check("the seed's date moves", await retirement_dates_backfill.apply(s, date(2026, 11, 2)), 1)
            check("to the year they turn 70", (goal.target_date, goal.assumptions["years"], goal.assumptions["funding_share"]), (date(2028, 1, 1), 2, 0.8))
            check("and only once", await retirement_dates_backfill.apply(s, date(2026, 11, 2)), 0)
            goal.target_date, goal.assumptions = date(2031, 1, 1), {"years": 5}
            await s.flush()
            check("a date a person moved is left", await retirement_dates_backfill.apply(s, date(2026, 11, 2)), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
