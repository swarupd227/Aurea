"""Prove Abandonment Recovery raises an onboarding account only when it has stayed in its state past that state's SLA,
names the state, its owner, the SLA and how far over, leaves an account inside its SLA alone however long since anyone
touched it, and that approving adds a task for the owner and changes nothing on the account.

Runs against a throwaway SQLite database; the note is the agent's fallback, so no model is called.

    python -m tests.verify_stalled_onboarding
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import abandonment_recovery as ar
from app.atlas.base import AgentContext, Subject
from app.core.db import Base, utcnow
from app.models.engagement import Task
from app.models.governance import AgentRun
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def _fallback(ctx, *, fallback, **kw):
    return fallback


def pure() -> None:
    print("\n=== Which accounts are late ===")
    row = dict(account="A", state="documents_generated", owner="Adviser", sla_days=5, age_days=9.2, overdue=True, exceptions=[], step=3, steps=10)
    rows = ar.overdue_rows([row, {**row, "account": "B", "age_days": 4.0, "overdue": False},
                            {**row, "account": "C", "state": "invested", "sla_days": None, "age_days": 40.0, "overdue": False},
                            {**row, "account": "D", "state": "in_review", "owner": "Compliance", "sla_days": 10, "age_days": 22.0}])
    check("only those past their state's SLA, furthest over first", [(r["account"], r["days_over"]) for r in rows], [("D", 12.0), ("A", 4.2)])
    check("each says what moves it on", rows[1]["next_step"], "Chase the client's signature")


async def main() -> int:
    pure()
    ar.narrate = _fallback
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            s.add(firm)
            await s.flush()
            now = utcnow()

            def case(name, **kw):
                c = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name=name, registration_type="individual",
                                   intake={"household": name}, created_at=now - timedelta(days=30), updated_at=now - timedelta(days=30), **kw)
                s.add(c)
                return c
            late = case("Avery", agreement_sent_at=now - timedelta(days=9))
            case("Blake", agreement_sent_at=now - timedelta(days=2))   # untouched for 30 days, but two days into a five-day SLA
            await s.flush()

            run = AgentRun(firm_id=firm.id, agent_key="abandonment_recovery", status="sensing", tier="tier_1", trigger="schedule", started_at=now)
            s.add(run)
            await s.flush()
            ctx = AgentContext(session=s, firm=firm, run=run, tier="tier_1", guardrails={}, config={}, subject=Subject("firm", firm.id, firm.name))
            agent = ar.AbandonmentRecoveryAgent()
            sensed = await agent.sense(ctx)
            drafts = await agent.think(ctx, sensed)
            print("\n=== The proposal ===")
            check("one account raised: the one past its SLA", [d.subject.label for d in drafts], ["Avery"])
            d = drafts[0]
            p = d.payload
            check("it names the state, its owner, the SLA and how far over",
                  (p["state_label"], p["owner"], p["sla_days"], round(p["days_over"])), ("Out for signature", "Adviser", 5, 4))
            check("the title carries the state and the days over", d.title, "Past its SLA — Avery: out for signature (4d over)")
            check("the note says whose move it is and what it is", ("Adviser's move" in d.rationale or "adviser's move" in d.rationale,
                                                                     "chase the client's signature" in d.rationale), (True, True))

            print("\n=== Approving ===")
            rec = type("Rec", (), {"payload": p, "modified_payload": None, "subject_id": late.id, "subject_label": "Avery", "decided_by": None})()
            out = await agent.act(ctx, rec)
            task = (await s.execute(select(Task))).scalars().one()
            check("a task for the owner, due today", (out["executed"], task.title, task.source),
                  (True, "Avery: Chase the client's signature (Adviser)", "abandonment_recovery"))
            await s.refresh(late)
            check("the account itself is untouched", (late.agreement_signed_at, late.status), (None, "intake"))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
