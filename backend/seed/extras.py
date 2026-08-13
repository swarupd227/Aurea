"""Patch an existing demo DB with missing extra households.

Run this when new households were added to EXTRA_HOUSEHOLDS after the initial seed:
    python -m seed.extras

Safe to run repeatedly — skips households that already exist by name.
"""
from __future__ import annotations

import asyncio
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Household, User
from app.models.portfolio import Instrument, ModelPortfolio
from app.models.tenant import Firm

from seed.run import EXTRA_HOUSEHOLDS, _extra_household

log = get_logger("aurea.seed.extras")


async def patch() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if not firm:
            log.error("extras_no_firm", msg="Demo firm not found — run seed.run first")
            return

        adviser = (await s.execute(
            select(User).where(User.firm_id == firm.id, User.email == "sophie.adviser@aurea.demo")
        )).scalar_one_or_none()
        if not adviser:
            log.error("extras_no_adviser")
            return

        existing_names = set(
            (await s.execute(select(Household.name).where(Household.firm_id == firm.id))).scalars().all()
        )

        instruments_rows = (await s.execute(
            select(Instrument).where(Instrument.firm_id == firm.id)
        )).scalars().all()
        instruments = {i.symbol: i for i in instruments_rows}

        portfolios = (await s.execute(
            select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
        )).scalars().all()
        balanced = next((p for p in portfolios if "balanced" in p.name.lower()), portfolios[0])
        growth = next((p for p in portfolios if "growth" in p.name.lower()), portfolios[0])

        added = 0
        for spec in EXTRA_HOUSEHOLDS:
            if spec["name"] in existing_names:
                log.info("extras_skip", name=spec["name"])
                continue
            log.info("extras_add", name=spec["name"])
            await _extra_household(s, firm, adviser, instruments, balanced, growth, spec)
            added += 1

        await s.commit()
        log.info("extras_done", added=added, skipped=len(EXTRA_HOUSEHOLDS) - added)


if __name__ == "__main__":
    configure_logging()
    asyncio.run(patch())
