"""Add Morrison (US) and Hartley (UK) households to an already-seeded demo firm."""
from __future__ import annotations
import asyncio
from datetime import date

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Household
from app.models.identity import User
from app.models.portfolio import Instrument, ModelPortfolio
from app.models.tenant import Firm
from app.aurea_core.valuation import revalue_firm
from seed.run import _extra_household, EXTRA_HOUSEHOLDS

configure_logging()
log = get_logger("aurea.seed.us_uk")

US_UK_NAMES = {"The Morrison Family", "The Hartley Family"}
US_UK_SPECS = [s for s in EXTRA_HOUSEHOLDS if s["name"] in US_UK_NAMES]


async def main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if not firm:
            log.error("Demo firm not found — run the main seed first")
            return

        adviser = (await s.execute(
            select(User).where(User.firm_id == firm.id, User.email == "sophie.adviser@aurea.demo")
        )).scalar_one_or_none()
        if not adviser:
            log.error("Adviser user not found")
            return

        instruments_rows = (await s.execute(
            select(Instrument).where(Instrument.firm_id == firm.id)
        )).scalars().all()
        instruments = {i.symbol: i for i in instruments_rows}

        models = (await s.execute(
            select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
        )).scalars().all()
        balanced = next((m for m in models if "Balanced" in m.name), models[0])
        growth   = next((m for m in models if "Growth"   in m.name), models[-1])

        existing_names = set(
            (await s.execute(
                select(Household.name).where(Household.firm_id == firm.id)
            )).scalars().all()
        )

        added = 0
        for spec in US_UK_SPECS:
            if spec["name"] in existing_names:
                log.info("skip_exists", household=spec["name"])
                continue
            log.info("adding_household", household=spec["name"])
            await _extra_household(s, firm, adviser, instruments, balanced, growth, spec)
            added += 1

        await s.commit()
        log.info("done", added=added)

    if added:
        async with SessionLocal() as s:
            firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
            await revalue_firm(s, firm.id)
            await s.commit()
            log.info("revalued")


asyncio.run(main())
