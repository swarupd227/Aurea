"""Take the invented portfolio figure out of seeded meeting briefs.

engage_extra seeded each demo meeting's brief with "portfolio": {"total_value": 0}, so a meeting
for a household holding millions showed a portfolio of $0. Nothing valued the book for those
briefs; the figure is removed, and the meeting view shows "—" until meeting prep writes a real
one. A brief meeting prep wrote always carries the asset-class breakdown beside its total, so
only the seeded shape (a zero total and nothing else) is touched. Idempotent.

    python -m seed.meeting_brief_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.engagement import Meeting

log = get_logger("aurea.seed.meeting_brief_backfill")


def is_seeded_zero(brief: dict | None) -> bool:
    portfolio = (brief or {}).get("portfolio")
    return isinstance(portfolio, dict) and portfolio == {"total_value": 0}


async def backfill() -> None:
    async with SessionLocal() as s:
        fixed = 0
        for m in (await s.execute(select(Meeting))).scalars().all():
            if is_seeded_zero(m.brief):
                m.brief = {k: v for k, v in m.brief.items() if k != "portfolio"}
                fixed += 1
        await s.commit()
        log.info("meeting_briefs_fixed", fixed=fixed)
        print(f"\n{fixed} seeded meeting brief(s) no longer claim a portfolio of 0.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
