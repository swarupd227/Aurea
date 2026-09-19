"""Seed one illustrative corporate action per firm (L200-4 §5) for firms that predate
these tables.

Deliberately left in "announced" status with no entitlements computed and no posting —
this backfill introduces a new, empty operational queue for the ops desk to work, it does
not silently mutate anyone's cash balance or tax lots. Computing entitlements and posting
are explicit, auditable API actions (POST /api/corporate-actions/...), not something a
backfill script should do on a client's book without a human in the loop.

Idempotent: skips a firm that already has any CorporateAction row.

    python -m seed.corporate_actions_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.corporate_actions import CorporateAction
from app.models.portfolio import Instrument
from app.models.tenant import Firm

log = get_logger("aurea.seed.corporate_actions_backfill")


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        created = 0

        for firm in firms:
            existing = (await s.execute(
                select(CorporateAction).where(CorporateAction.firm_id == firm.id)
            )).scalar_one_or_none()
            if existing is not None:
                continue

            instrument = (await s.execute(
                select(Instrument).where(Instrument.firm_id == firm.id, Instrument.asset_class == "equity")
            )).scalars().first()
            if instrument is None:
                continue

            today = utcnow().date()
            s.add(CorporateAction(
                firm_id=firm.id, instrument_id=instrument.id, action_type="cash_dividend",
                status="announced", is_voluntary=False,
                record_date=today + timedelta(days=10),
                ex_date=today + timedelta(days=9),
                payable_date=today + timedelta(days=30),
                details={"cash_per_share": 0.42},
                source="mock_dtc_feed",
            ))
            created += 1
            log.info("corporate_action_seeded", firm=firm.slug, instrument=instrument.symbol)

        await s.commit()
        print(f"\nSeeded {created} corporate action(s) (announced, no entitlements computed).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
