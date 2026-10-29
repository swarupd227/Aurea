"""Make each seeded composite apply the seasoning its inclusion criteria state.

composites_backfill seeded every composite with seasoning_days=0 while its criteria said new
accounts are seasoned 90 days, so a new account joined at once. composite_report now seasons only
an account opened after the composite was built (the back-book it was built from counts at once),
which lets the stated 90 days apply. This sets 0 to 90 and replaces the earlier seeded wording
with the wording that says so. Criteria a person wrote are left alone. Idempotent.

    python -m seed.composite_seasoning_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.aurea_core import composites as engine
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.portfolio import ModelPortfolio
from seed.composites_backfill import SEASONING_DAYS, criteria_for

log = get_logger("aurea.seed.composite_seasoning_backfill")


def earlier_seeded_criteria(model_name: str) -> str:
    return (f"All discretionary, fee-paying accounts managed to the '{model_name}' model for the full "
            "reporting period, from the date of firm inception. New accounts are seasoned 90 days before "
            "inclusion. No minimum account size.")


async def backfill() -> None:
    async with SessionLocal() as s:
        fixed = 0
        for c in (await s.execute(select(Composite))).scalars().all():
            model = await s.get(ModelPortfolio, c.model_portfolio_id)
            changed = False
            if c.seasoning_days == 0:
                c.seasoning_days = SEASONING_DAYS
                changed = True
            if model and c.inclusion_criteria == earlier_seeded_criteria(model.name):
                c.inclusion_criteria = criteria_for(model.name)
                changed = True
            if changed:
                fixed += 1
            await s.flush()
            r = await engine.composite_report(s, c)
            print(f"{c.name}: seasoning {c.seasoning_days} days, built {r['constructed_on']}, "
                  f"{r['accounts_included']} included, waiting: {r['accounts_excluded_unseasoned'] or 'none'}")
        await s.commit()
        log.info("composite_seasoning_fixed", fixed=fixed)
        print(f"\n{fixed} composite(s) now apply the seasoning they state.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
