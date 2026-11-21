"""The planning engines are on the AI and model inventory (B-189, L200-2 §4.1).

    .venv\\Scripts\\python.exe -m tests.verify_model_inventory

Each engine that answers a client's question with a model (retirement, goal likelihood, the financial plan, protection)
is a "model" use case with an owner, a risk tier and a review; the inventory shows its method and the version of the
assumption set it runs on, a fingerprint that changes when the firm's assumptions do; one never reviewed past its
cadence is overdue; and the backfill that adds them is idempotent.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import ai_governance, planning
from app.core.db import Base, utcnow
from app.models.ai_governance import USE_CASE_CATEGORIES, AIUseCase
from app.models.tenant import Firm
from seed import ai_governance_backfill as backfill

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def main_async() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Hudson", slug="hudson", jurisdiction="US", base_currency="USD", settings={})
            s.add(firm)
            await s.flush()

            print("\n=== The engines go on the inventory, once ===")
            check("a planning engine is a category of its own", "model" in USE_CASE_CATEGORIES, True)
            first = await backfill.seed_firm(s, firm)
            await s.flush()
            again = await backfill.seed_firm(s, firm)
            check("the backfill adds every row, then nothing on a second run", (first > 4, again), (True, 0))
            rows = (await s.execute(select(AIUseCase).where(AIUseCase.firm_id == firm.id, AIUseCase.category == "model"))).scalars().all()
            check("the four engines", sorted(r.use_case_key for r in rows),
                  ["financial_plan_engine", "goal_likelihood_engine", "protection_engine", "retirement_engine"])
            check("owned by the head of advice, who sets their assumptions", {r.owner for r in rows}, {"Head of Advice"})
            check("the two a client sees on Canvas are high risk",
                  {r.use_case_key for r in rows if r.risk_tier == "high"}, {"retirement_engine", "goal_likelihood_engine"})

            print("\n=== Each shows its method, its review and the assumptions it runs on ===")
            summary = await ai_governance.inventory_summary(s, firm.id)
            models = {m["use_case_key"]: m for m in summary["models"]}
            ret = models["retirement_engine"]
            check("its method and module", (ret["module"], "Monte Carlo" in ret["method"]), ("aurea_core/retirement.py", True))
            check("the platform's assumptions while the firm has set none", ret["assumptions"]["source"], "platform")
            v1 = ret["assumptions"]["version"]
            check("a ten-character assumption version", len(v1), 10)
            check("never reviewed, so not reviewed yet but not overdue on day one", (ret["last_reviewed_at"], ret["overdue"]), (None, False))

            firm.settings = {"cmas": {"equity": [0.02, 0.15]}}
            await s.flush()
            moved = {m["use_case_key"]: m for m in (await ai_governance.inventory_summary(s, firm.id))["models"]}["retirement_engine"]
            check("setting a CMA makes them the firm's and changes the version",
                  (moved["assumptions"]["source"], moved["assumptions"]["version"] != v1), ("firm", True))
            check("the version is the same for the same assumptions", planning.assumption_version(firm.settings), moved["assumptions"]["version"])

            for r in rows:
                r.created_at = utcnow() - timedelta(days=200)
            reviewed = next(r for r in rows if r.use_case_key == "protection_engine")
            reviewed.last_reviewed_at = utcnow() - timedelta(days=10)
            await s.flush()
            after = {m["use_case_key"]: m for m in (await ai_governance.inventory_summary(s, firm.id))["models"]}
            check("past its 180-day cadence with no review, an engine is overdue", after["retirement_engine"]["overdue"], True)
            check("one reviewed ten days ago is not, and says when it is next due",
                  (after["protection_engine"]["overdue"], after["protection_engine"]["review_due"] is not None), (False, True))
            overdue_keys = {o["use_case_key"] for o in (await ai_governance.inventory_summary(s, firm.id))["overdue_review"]}
            check("and the overdue list names it", "retirement_engine" in overdue_keys and "protection_engine" not in overdue_keys, True)
    finally:
        await db.dispose()


def main() -> int:
    asyncio.run(main_async())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
