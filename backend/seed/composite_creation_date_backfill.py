"""Give each seeded composite the creation date it was actually built on.

The two deployed composites state a creation date of 24 Sep 2024 but their rows were written on
24 Sep 2026, and the page shows the stated date as fact - two years of composite history that does
not exist. composites_backfill writes the day it runs, so this corrects only a seeded composite
(its criteria are the seeded wording) whose stated date is earlier than the day it was built. A
composite a person created keeps the date they gave it: a firm bringing an existing composite
across may rightly date it before it reached this platform. Idempotent.

    python -m seed.composite_creation_date_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.portfolio import ModelPortfolio
from seed.composites_backfill import criteria_for

log = get_logger("aurea.seed.composite_creation_date_backfill")


def corrected(c: Composite, model_name: str | None) -> date | None:
    """The date a seeded composite was built, if it claims an earlier one; otherwise None."""
    if model_name is None or c.created_at is None or c.inclusion_criteria != criteria_for(model_name):
        return None
    built = c.created_at.date()
    return built if c.creation_date < built else None


async def backfill() -> None:
    async with SessionLocal() as s:
        fixed = 0
        for c in (await s.execute(select(Composite))).scalars().all():
            model = await s.get(ModelPortfolio, c.model_portfolio_id)
            when = corrected(c, model.name if model else None)
            if when is not None:
                print(f"{c.name}: creation date {c.creation_date} -> {when}")
                c.creation_date = when
                fixed += 1
        await s.commit()
        log.info("composite_creation_dates_fixed", fixed=fixed)
        print(f"\n{fixed} seeded composite(s) now state the day they were built.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
