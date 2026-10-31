"""Carry meetings already on file into the review record.

    python -m seed.client_review_backfill

A completed meeting called a review was held, so it is carried across on the day it was held. Nobody
said what it covered, so `covered` stays empty and the record reads "not recorded" rather than guessing.
A meeting not called a review is left for a person to record.

Idempotent: a meeting already carried and a day already recorded are left alone. Every firm.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, reviews
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.engagement import ClientReview, Meeting
from app.models.enums import MeetingStatus
from app.models.tenant import Firm

log = get_logger("aurea.seed.client_review_backfill")


async def apply(session: AsyncSession) -> int:
    """Create what is absent. Returns the number of reviews created."""
    have = (await session.execute(select(ClientReview.household_id, ClientReview.held_on, ClientReview.meeting_id))).all()
    days = {(household_id, held_on) for household_id, held_on, _ in have}
    carried = {meeting_id for _, _, meeting_id in have}
    created = 0
    meetings = (await session.execute(select(Meeting).where(Meeting.status == MeetingStatus.COMPLETED.value))).scalars().all()
    for m in meetings:
        if not m.scheduled_at or not reviews.is_review(m) or m.id in carried:
            continue
        held_on = firm_time.today(await session.get(Firm, m.firm_id), m.scheduled_at)
        if (m.household_id, held_on) in days:
            continue
        session.add(ClientReview(firm_id=m.firm_id, household_id=m.household_id, meeting_id=m.id, held_on=held_on,
                                 covered=[], recorded_by="unknown", recorded_by_role="unknown"))
        days.add((m.household_id, held_on))
        created += 1
        log.info("client_review_backfilled", meeting=m.title, held_on=held_on.isoformat())
    await session.flush()
    return created


async def backfill() -> None:
    async with SessionLocal() as s:
        created = await apply(s)
        await s.commit()
        print(f"\nCarried across {created} review(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
