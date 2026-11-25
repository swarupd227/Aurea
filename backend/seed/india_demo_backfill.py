"""Bring the deployed Indian demonstration firm up to what `seed/india_demo.py` now writes.

The firm's goals were sized without contributions, so a family on a high income showed none of its
goals on track, and its tax memos were written before their prompt named the household properly.
This resets the goals from the seed and reruns the tax agent, which refreshes its open proposals
rather than raising more. It touches no other firm. Safe to run twice.

Row 7 (a non-resident household and a minor), row 8 (a Portfolio Manager client's agreement and fee), row 9 (a consolidated
statement) and row 10 (a complaints register) are added once. The simulated Account Aggregator is not written here: it is
opt-in (`python -m app.simulations seed aa_feed`, with AUREA_SIMULATIONS on).

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
from seed.india_demo import HOUSEHOLDS, INSTRUMENTS, SLUG, _closes_or_fallback, add_instrument


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


async def add_funds(s, firm: Firm) -> int:
    """Row 4: the mutual funds the seed now carries, added where the deployed firm lacks them. They have no
    price until the sync below fetches the real NAV from AMFI."""
    from app.models.portfolio import Instrument

    have = set((await s.execute(select(Instrument.symbol).where(Instrument.firm_id == firm.id))).scalars().all())
    closes = _closes_or_fallback(None)
    added = 0
    for sym, spec in INSTRUMENTS.items():
        if spec[4].get("amfi_code") and sym not in have:
            await add_instrument(s, firm, sym, closes[sym])
            added += 1
    return added


async def main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none()
        if firm is None:
            print("\nNo Indian demonstration firm here. Run python -m seed.india_demo first.\n")
            return
        changed = await reset_goals(s, firm)
        funds = await add_funds(s, firm)
        from seed import india_row5

        row5 = await india_row5.add(s, firm)
        from seed import india_row6

        row6 = await india_row6.add(s, firm)
        row6 += await india_row6.correct_names(s, firm)
        from seed import india_row7, india_row8

        row7 = await india_row7.add(s, firm)
        row8 = await india_row8.add(s, firm)
        from seed import india_row9, india_row10

        row9 = await india_row9.add(s, firm)
        row10 = await india_row10.add(s, firm)
        await s.commit()
        from app.conduit.service import sync_market_data
        priced = await sync_market_data(s, firm.id)
        await s.commit()
        await run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                        subject=Subject("firm", firm.id, firm.name), require_human=True)
        await s.commit()
    print(f"\nReset {changed} goal(s), added {funds} fund(s), {row5} row 5, {row6} row 6, {row7} row 7, {row8} row 8, {row9} row 9 and {row10} row 10 record(s), priced {priced['resolved']} instrument(s) "
          f"({len(priced['failed'])} failed), and refreshed the tax positions.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
