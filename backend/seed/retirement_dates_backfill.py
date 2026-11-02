"""Bring two seeded retirement goals back to a working life that makes sense.

    python -m seed.retirement_dates_backfill

The seed set James Hartley's retirement ten years out and Robert Morrison's eight, so they worked to 77 and 74, and
that sets their protection figures. Both are invented clients. Each now retires in the year they turn 70, the goal's
projection follows the date, and nothing else changes.

Idempotent: a goal already on its date is left alone. A retirement goal a person has moved is theirs: only the date
the seed wrote is corrected, so a household whose goal does not sit on it is left alone too.
"""
from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Goal, Household, Person

log = get_logger("aurea.seed.retirement_dates_backfill")
RETIRES_AT = 70
CLIENTS = {"James Hartley": 10, "Robert Morrison": 8}   # name -> years ahead the seed wrote


async def apply(session: AsyncSession, today: date | None = None) -> int:
    """Move what is on the seed's date. Returns the number of goals moved."""
    today = today or date.today()
    moved = 0
    for name, seeded_years in CLIENTS.items():
        people = (await session.execute(select(Person).where(Person.full_name == name))).scalars().all()
        for person in people:
            if person.date_of_birth is None:
                continue
            target = date(person.date_of_birth.year + RETIRES_AT, 1, 1)
            goals = (await session.execute(select(Goal).where(Goal.household_id == person.household_id, Goal.kind == "retirement"))).scalars().all()
            for goal in goals:
                if goal.target_date == target or (goal.assumptions or {}).get("years") != seeded_years:
                    continue
                household = await session.get(Household, person.household_id)
                goal.target_date = target
                goal.assumptions = {**(goal.assumptions or {}), "years": max(target.year - today.year, 1)}
                flag_modified(goal, "assumptions")
                moved += 1
                log.info("retirement_date_moved", household=household.name if household else None, to=target.isoformat())
    await session.flush()
    return moved


async def backfill() -> None:
    async with SessionLocal() as s:
        moved = await apply(s)
        await s.commit()
        print(f"\nMoved {moved} retirement goal(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
