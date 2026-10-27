"""Close the duplicate proposals scheduled agents left in a firm's queue.

Before atlas/proposals.py, every daily run raised the same proposal again, so one stalled
onboarding case could be in the queue sixty times. From now on a later run refreshes the open
proposal; this clears what had already piled up. For each subject it keeps the proposals from
the newest run and expires the older ones, and the ledger records each expiry and which
proposal replaced it. Nobody's decision is overwritten: only PROPOSED rows are touched.

Covers every agent that keeps one open proposal per subject - scheduled runs refresh it, and a
run a person asks for replaces it - so it also clears a brief prepared twice by hand. Agents
whose every run stands alone (billing) are left out. A proposal someone already decided is left
alone. Idempotent: a second run finds nothing to close.

    python -m seed.proposals_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.atlas import proposals
from app.atlas.registry import build_registry
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.tenant import Firm

log = get_logger("aurea.seed.proposals_backfill")


async def backfill() -> None:
    one_per_subject = {k for k, a in build_registry().items() if a.refreshes_open_proposals}
    async with SessionLocal() as s:
        for firm in (await s.execute(select(Firm))).scalars().all():
            report = await proposals.collapse_open_duplicates(s, firm_id=firm.id, agent_keys=one_per_subject)
            await s.commit()
            log.info("proposals_collapsed", firm=firm.slug, **{k: v for k, v in report.items() if k != "by_agent"})
            print(f"\n{firm.slug}: {report['open_before']} open proposal(s) -> {report['kept']} kept, "
                  f"{report['expired']} duplicate(s) closed")
            for agent, n in sorted(report["by_agent"].items(), key=lambda kv: -kv[1]):
                print(f"    {agent:28s} {n}")
        print()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
