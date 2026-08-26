"""Apply default agent cadences to firms seeded before scheduling was config-driven.

    python -m seed.schedule_backfill

Only fills a cadence where none is set — a firm that has already tuned an agent in Admin
keeps its own setting. Safe to run repeatedly.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.agents import schedules as agent_schedules
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.tenant import AgentConfig, Firm

log = get_logger("aurea.seed.schedule_backfill")


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        applied = skipped = 0

        for firm in firms:
            configs = (await s.execute(
                select(AgentConfig).where(AgentConfig.firm_id == firm.id)
            )).scalars().all()
            by_key = {str(c.agent_key): c for c in configs}

            for key, (cron, _why) in agent_schedules.DEFAULT_SCHEDULES.items():
                cfg = by_key.get(str(key))
                if not cfg:
                    log.warning("backfill_no_config", firm=firm.slug, agent=str(key))
                    continue
                if cfg.schedule_cron:
                    skipped += 1
                    continue
                cfg.schedule_cron = cron
                cfg.schedule_enabled = True
                applied += 1
                log.info("backfill_schedule", firm=firm.slug, agent=str(key), cron=cron)

        await s.commit()

        print(f"\nApplied {applied} cadence(s); left {skipped} existing setting(s) alone.\n")
        print(f"{'agent':28}{'cron':16}{'enabled':9}why")
        print("-" * 100)
        for key, (cron, why) in agent_schedules.DEFAULT_SCHEDULES.items():
            cfg = (await s.execute(
                select(AgentConfig).where(AgentConfig.agent_key == str(key)).limit(1)
            )).scalar_one_or_none()
            enabled = "yes" if (cfg and cfg.schedule_enabled) else "no"
            print(f"{str(key):28}{cron:16}{enabled:9}{why[:56]}")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
