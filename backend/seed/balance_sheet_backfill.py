"""Carry what clients declared they hold elsewhere at onboarding onto their household.

    python -m seed.balance_sheet_backfill

A case that was activated declared what it held away, but that stayed on the case, so it never reached the
household it became. Each declared figure becomes an asset on file, marked as declared at onboarding. A
declaration with no value is left behind, and no liability or expense is invented: those need a document.

Idempotent: an item already carried is left alone. Every firm.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, firm_time
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Household
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

log = get_logger("aurea.seed.balance_sheet_backfill")


async def apply(session: AsyncSession) -> int:
    """Create what is absent. Returns the number of items created."""
    created = 0
    for case in (await session.execute(select(OnboardingCase))).scalars().all():
        household_id = (case.materialized or {}).get("household_id")
        if not household_id or await session.get(Household, uuid.UUID(household_id)) is None:
            continue
        firm = await session.get(Firm, case.firm_id)
        carried = await balance_sheet.carry_from_onboarding(session, case, household_id=uuid.UUID(household_id),
                                                            today=firm_time.today(firm))
        if carried:
            created += carried
            log.info("balance_items_backfilled", case=case.prospect_name, items=carried)
    return created


async def backfill() -> None:
    async with SessionLocal() as s:
        created = await apply(s)
        await s.commit()
        print(f"\nCarried across {created} declared asset(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
