"""Record an assessed capacity for loss on mandates seeded before the field was set.

    python -m seed.capacity_backfill

seed/run.py skips a firm that already exists, so changing what it writes does nothing for
a database already in service. This applies the same values to the mandates already there.

It also clears the drift agent's pause, but only if the pause was caused by exactly this
defect — an equity ceiling derived from a capacity nobody had assessed. Any other pause is
a real supervisory decision and is left alone.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.enums import AgentKey
from app.models.graph import Mandate
from app.models.tenant import AgentConfig, Firm

log = get_logger("aurea.seed.capacity_backfill")

ASSESSED = {"conservative": "low", "balanced": "medium", "growth": "high"}

# The pause this is entitled to clear. Matched on substance, not on the whole string.
PAUSE_FINGERPRINT = "exceeds risk capacity limit"


async def backfill() -> None:
    async with SessionLocal() as s:
        mandates = (await s.execute(select(Mandate))).scalars().all()
        set_now, already = 0, 0

        for m in mandates:
            suit = dict(m.suitability or {})
            if suit.get("capacity_for_loss"):
                already += 1
                continue
            profile = (suit.get("risk_profile") or "").strip().lower()
            capacity = ASSESSED.get(profile, "medium")
            suit["capacity_for_loss"] = capacity
            # Replace the dict rather than mutating it — SQLAlchemy does not track an
            # in-place change to a JSON column and the write would be silently dropped.
            m.suitability = suit
            flag_modified(m, "suitability")
            set_now += 1
            log.info("capacity_assessed", mandate=m.name,
                     risk_profile=profile or "(none)", capacity_for_loss=capacity)

        print(f"\nAssessed capacity on {set_now} mandate(s); {already} already had one.\n")
        print(f"{'mandate':44}{'risk profile':16}{'capacity for loss'}")
        print("-" * 82)
        for m in mandates:
            suit = m.suitability or {}
            print(f"{m.name[:43]:44}{(suit.get('risk_profile') or '-'):16}"
                  f"{suit.get('capacity_for_loss') or '-'}")

        # Clear the pause this defect caused, and only that one.
        cleared = []
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            cfg = (await s.execute(
                select(AgentConfig).where(
                    AgentConfig.firm_id == firm.id,
                    AgentConfig.agent_key == AgentKey.DRIFT_REBALANCING,
                )
            )).scalar_one_or_none()
            if not cfg or not cfg.paused:
                continue
            reason = cfg.paused_reason or ""
            if PAUSE_FINGERPRINT in reason:
                cfg.paused = False
                cfg.paused_reason = None
                cleared.append(firm.slug)
                log.info("drift_unpaused", firm=firm.slug)
            else:
                print(f"\nLeft {firm.slug} drift paused — the reason is not this defect:\n"
                      f"  {reason}")

        await s.commit()

        if cleared:
            print(f"\nCleared the capacity-breach pause on drift for: {', '.join(cleared)}.")
            print("Drift will run again on its next scheduled sweep (every 6 hours).")
        else:
            print("\nNo drift pause matching this defect was found.")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
