"""Bring the deployed Indian demonstration firm up to what `seed/india_demo.py` now writes.

The firm's goals were sized without contributions, so a family on a high income showed none of its
goals on track, and its tax memos were written before their prompt named the household properly.
This resets the goals from the seed and reruns the tax agent, which refreshes its open proposals
rather than raising more. It touches no other firm. Safe to run twice.

Run: python -m seed.india_demo_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

import app.models  # noqa: F401
from app.atlas.base import Subject
from app.atlas.runtime import run_agent
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models.enums import AgentKey
from app.models.graph import Goal, Household
from app.models.tenant import Firm
from seed.india_demo import HOUSEHOLDS, SLUG


async def reset_goals(s, firm: Firm) -> int:
    """Set each seeded goal's target and assumptions, by household and goal name."""
    changed = 0
    for spec in HOUSEHOLDS:
        household = (await s.execute(select(Household).where(
            Household.firm_id == firm.id, Household.name == spec["name"]))).scalar_one_or_none()
        if household is None:
            continue
        for name, _kind, target, years, assumptions in spec["goals"]:
            goal = (await s.execute(select(Goal).where(
                Goal.firm_id == firm.id, Goal.household_id == household.id, Goal.name == name))).scalar_one_or_none()
            if goal is None:
                continue
            goal.target_amount = target
            goal.assumptions = {"years": years, **assumptions}
            changed += 1
    return changed


async def main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none()
        if firm is None:
            print("\nNo Indian demonstration firm here. Run python -m seed.india_demo first.\n")
            return
        changed = await reset_goals(s, firm)
        await s.commit()
        await run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                        subject=Subject("firm", firm.id, firm.name), require_human=True)
        await s.commit()
    print(f"\nReset {changed} goal(s) and refreshed the tax positions.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
