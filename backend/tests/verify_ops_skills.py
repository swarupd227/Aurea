"""Skills for operations beyond advice: marketing and finance (B-205).

    python -m tests.verify_ops_skills

Checks that the firm library gains the two skills, idempotently, and that what a skill reads about a household now
carries what those need, only from what is on file: each person's age and life stage, money held elsewhere, the
mandates' types, and the last fees as billed with the value and rate each was worked on, and credit notes.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.aurea_core.graph import household_brain
from app.core.db import Base
from app.models.billing import BillingRun, Invoice
from app.models.skill import Skill
from app.skills import runtime as skills
from seed.skills_seed import seed_skills
from tests import _sim_fixture

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            fx = await _sim_fixture.build(s)
            firm = fx.firm
            chen = fx.households["The Chen Family"]
            nguyen = fx.households["The Nguyen Family"]

            print("\n=== The firm library ===")
            added = await seed_skills(s, firm)
            again = await seed_skills(s, firm)
            names = {k.name for k in (await s.execute(select(Skill).where(Skill.firm_id == firm.id))).scalars().all()}
            check("both skills are in the firm library, once", (added, again, {"Retirement seminar invitations", "Fee variance check"} <= names),
                  (5, 0, True))

            print("\n=== What a skill reads ===")
            today = date.today()
            run = BillingRun(firm_id=firm.id, as_of=today, status="issued")
            s.add(run)
            await s.flush()
            for start, amount, value in ((today - timedelta(days=200), 1200.0, 480_000), (today - timedelta(days=110), 1500.0, 600_000)):
                s.add(Invoice(firm_id=firm.id, run_id=run.id, kind="invoice", household_id=chen["household"].id, mandate_name="Growth",
                              household_name="The Chen Family", period_start=start, period_end=start + timedelta(days=90),
                              amount=amount, status="issued", calculation={"billable_value": value, "effective_bps": 100.0}))
            s.add(Invoice(firm_id=firm.id, run_id=run.id, kind="credit_note", household_id=chen["household"].id, mandate_name="Growth",
                          household_name="The Chen Family", period_start=today - timedelta(days=110), period_end=today - timedelta(days=20),
                          amount=-150.0, status="issued", calculation={}))
            s.add(Invoice(firm_id=firm.id, run_id=run.id, kind="invoice", household_id=chen["household"].id, mandate_name="Growth",
                          household_name="The Chen Family", period_start=today - timedelta(days=20), period_end=today + timedelta(days=70),
                          amount=999.0, status="draft", calculation={}))
            await s.flush()
            brain = await household_brain(s, chen["household"].id, firm_id=firm.id)
            facts = await skills._facts(s, firm, brain)
            ages = [f for f in facts if f.startswith("Person:")]
            check("each person's age, from their date of birth on the firm's day", len(ages) == 2 and all("age " in a and "not on file" not in a for a in ages), True)
            fees = [f for f in facts if f.startswith("Fee billed")]
            check("the last two fees as billed, newest first, with the value and rate", (len(fees), "1,500" in fees[0] or "1500" in fees[0],
                                                                                         "600,000" in fees[0] or "600000" in fees[0], "100 bps" in fees[0]),
                  (2, True, True, True))
            check("a draft is not a fee billed", any("999" in f for f in fees), False)
            check("credit notes are counted", any(f.startswith("Credit notes in the last year: 1") for f in facts), True)
            check("held elsewhere: none on file says so", "Held elsewhere: none on file" in facts, True)

            brain2 = await household_brain(s, nguyen["household"].id, firm_id=firm.id)
            facts2 = await skills._facts(s, firm, brain2)
            check("a household with no fees on file says so", "Fees billed in the last year: none on file" in facts2, True)
            check("the mandate type is given", any(f.startswith("Mandates: ") for f in facts2), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
