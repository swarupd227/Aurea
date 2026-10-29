"""Move the seeded demo meetings to dates around today.

engage_extra dated its meetings from a fixed day (24 June 2026), and run.py dated the Chen
quarterly review three days after the seed ran. On a book seeded then, every "upcoming" meeting was
months overdue by the autumn. This moves each engage_extra meeting still on its seeded date to the
same offset from today (a completed one stays in the past, an upcoming one is upcoming again), and
moves the Chen review, if it was never held and its date has passed, to three days from today.
A meeting someone re-dated is left alone. Safe to run again.

    python -m seed.meeting_dates_backfill
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.engagement import Meeting
from seed.engage_extra import MEETINGS, NOW, SEEDED_FROM

log = get_logger("aurea.seed.meeting_dates_backfill")

CHEN_REVIEW = "Quarterly review — The Chen Family"


def _aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=SEEDED_FROM.tzinfo)


def new_date(m: Meeting, now) -> object | None:
    """Where a seeded meeting belongs today, or None if it is not a seeded one still on its seeded date."""
    when = _aware(m.scheduled_at)
    if when is None:
        return None
    for title, _status, days in MEETINGS:
        if m.title.startswith(f"{title} — ") and abs((when - (SEEDED_FROM + timedelta(days=days))).total_seconds()) < 1:
            return NOW + timedelta(days=days)
    if m.title == CHEN_REVIEW and m.date_passed(now):
        return (NOW + timedelta(days=3)).replace(hour=10)
    return None


async def backfill() -> None:
    now = utcnow()
    async with SessionLocal() as s:
        moved = 0
        for m in (await s.execute(select(Meeting))).scalars().all():
            when = new_date(m, now)
            if when is not None:
                print(f"{m.title}: {_aware(m.scheduled_at).date()} -> {when.date()}")
                m.scheduled_at = when
                moved += 1
        await s.commit()
        log.info("meeting_dates_moved", moved=moved)
        print(f"\n{moved} seeded meeting(s) moved to dates around today.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
