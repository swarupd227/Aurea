"""Bring an existing firm's agent configs up to date with the current catalogue.

    python -m seed.schedule_backfill

Two jobs, both idempotent and safe to run repeatedly:

  1. Create an AgentConfig row for any catalogue agent that has none. A firm seeded
     before an agent existed never got a row for it, and `seed/run.py` skips a firm
     that already exists — so those rows are never backfilled by a re-seed. Without a
     row the agent still runs on demand (the runtime tolerates cfg=None), but it can
     never be *scheduled*, because scheduling is driven by the config table.

  2. Fill in a starting cadence where none is set. A firm that has already tuned an
     agent in Admin keeps its own setting — an existing schedule_cron is never
     overwritten.

It deliberately does not enable, disable, pause or unpause anything that already
exists. Those are governance decisions and stay where the firm put them.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.agents import schedules as agent_schedules
from app.agents.catalogue import CATALOGUE
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.tenant import AgentConfig, Firm

log = get_logger("aurea.seed.schedule_backfill")


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        created = scheduled = kept = 0

        for firm in firms:
            configs = (await s.execute(
                select(AgentConfig).where(AgentConfig.firm_id == firm.id)
            )).scalars().all()
            by_key = {str(c.agent_key): c for c in configs}

            for key, meta in CATALOGUE.items():
                cron, _why = agent_schedules.DEFAULT_SCHEDULES.get(key, (None, None))
                cfg = by_key.get(str(key))

                if cfg is None:
                    # Matches what a fresh seed produces for this agent.
                    cfg = AgentConfig(
                        firm_id=firm.id, agent_key=key, enabled=True,
                        default_tier=meta["default_tier"],
                        schedule_cron=cron, schedule_enabled=bool(cron),
                    )
                    s.add(cfg)
                    created += 1
                    if cron:
                        scheduled += 1
                    log.info("backfill_created_config", firm=firm.slug,
                             agent=str(key), cron=cron or "on-demand")
                    continue

                if not cron:
                    continue
                if cfg.schedule_cron:
                    kept += 1
                    continue
                cfg.schedule_cron = cron
                cfg.schedule_enabled = True
                scheduled += 1
                log.info("backfill_schedule", firm=firm.slug, agent=str(key), cron=cron)

        await s.commit()

        print(f"\nCreated {created} missing config(s); set {scheduled} cadence(s); "
              f"left {kept} existing cadence(s) alone.\n")

        # Report what will actually fire, per firm, and why anything will not.
        for firm in firms:
            rows = (await s.execute(
                select(AgentConfig).where(AgentConfig.firm_id == firm.id)
            )).scalars().all()
            by_key = {str(c.agent_key): c for c in rows}

            print(f"{firm.slug}:")
            print(f"  {'agent':28}{'cron':16}{'will fire':11}why not")
            print("  " + "-" * 84)
            live = 0
            for key, (cron, _why) in agent_schedules.DEFAULT_SCHEDULES.items():
                c = by_key.get(str(key))
                blockers = []
                if c is None:
                    blockers.append("no config row")
                else:
                    if not c.enabled:
                        blockers.append("disabled")
                    if c.paused:
                        blockers.append(f"paused ({c.paused_reason or 'no reason given'})")
                    if not c.schedule_enabled:
                        blockers.append("schedule off")
                    if not c.schedule_cron:
                        blockers.append("no cron")
                fires = "yes" if not blockers else "NO"
                if not blockers:
                    live += 1
                print(f"  {str(key):28}{(c.schedule_cron if c else None) or cron:16}"
                      f"{fires:11}{', '.join(blockers)}")
            print(f"\n  {live}/{len(agent_schedules.DEFAULT_SCHEDULES)} scheduled agents "
                  f"will fire for {firm.slug}.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
