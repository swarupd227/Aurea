"""Carry IPS acceptances already on file into the record the book is monitored against.

    python -m seed.ips_backfill

An onboarding case that was accepted and activated accepted its mandate's model and constraints, so
those become the mandate's first version, dated when it was accepted and by whom. Every other mandate is
left with no IPS on file: their terms are in documents only a person can read.

Idempotent: a mandate with an IPS is left alone. Every firm.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, ips
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Mandate
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

log = get_logger("aurea.seed.ips_backfill")


async def apply(session: AsyncSession) -> int:
    """Create what is absent. Returns the number of IPS versions created."""
    created = 0
    cases = (await session.execute(select(OnboardingCase).where(OnboardingCase.ips_accepted_at.isnot(None)))).scalars().all()
    for case in cases:
        mandate_id = (case.materialized or {}).get("mandate_id")
        mandate = await session.get(Mandate, uuid.UUID(mandate_id)) if mandate_id else None
        if mandate is None:
            continue
        firm = await session.get(Firm, case.firm_id)
        if await ips.carry_from_onboarding(session, mandate, accepted_on=firm_time.today(firm, case.ips_accepted_at),
                                           by=case.ips_accepted_by, document=f"Accepted at onboarding: {case.prospect_name}"):
            created += 1
            log.info("ips_backfilled", mandate=mandate.name)
    await session.flush()
    return created


async def backfill() -> None:
    async with SessionLocal() as s:
        created = await apply(s)
        await s.commit()
        print(f"\nCarried across {created} IPS acceptance(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
