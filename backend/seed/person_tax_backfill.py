"""Give the demo firm's Chen family the tax details the seed gives them.

    python -m seed.person_tax_backfill

The seed added income, tax rates and properties for Wei and Mei Chen after the demo firm was first
created, and `seed/run.py` skips a firm that exists, so the deployed database never received them. The
tax planning and protection gaps read those fields and find nothing.

Idempotent, and it only fills what is absent: a person who already has tax details keeps them, so a
figure recorded from a real document is never overwritten by a seeded one. Demo firm only.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Household, Person
from app.models.tenant import Firm

log = get_logger("aurea.seed.person_tax_backfill")

# The same values as seed/run.py.
_DETAILS = {
    "Wei Chen": {
        "tax": {"marginal_rate": 0.33, "pir": 0.28, "kiwisaver_rate": 0.03, "annual_income": 280000},
        "properties": [
            {"address": "12 Harbour View Tce, Auckland", "acquired_on": "2024-11-15", "value": 1850000},
            {"address": "47 Lakeside Dr, Queenstown", "acquired_on": "2017-01-20", "value": 1200000},
        ],
    },
    "Mei Chen": {
        "tax": {"marginal_rate": 0.175, "pir": 0.175, "kiwisaver_rate": 0.03, "annual_income": 120000},
    },
}


async def apply(session: AsyncSession) -> int:
    """Fill what is absent. Returns the number of people changed."""
    firm = (await session.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
    if firm is None:
        return 0
    rows = (await session.execute(
        select(Person).join(Household, Household.id == Person.household_id)
        .where(Person.firm_id == firm.id, Household.name == "The Chen Family", Person.full_name.in_(_DETAILS))
    )).scalars().all()
    changed = 0
    for person in rows:
        profile = dict(person.profile or {})
        missing = {k: v for k, v in _DETAILS[person.full_name].items() if k not in profile}
        if not missing:
            continue
        person.profile = {**profile, **missing}
        flag_modified(person, "profile")
        changed += 1
        log.info("person_tax_backfilled", person=person.full_name, fields=sorted(missing))
    await session.flush()
    return changed


async def backfill() -> None:
    async with SessionLocal() as s:
        changed = await apply(s)
        await s.commit()
        print(f"\nFilled in {changed} person(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
