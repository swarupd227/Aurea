"""Give each model portfolio a benchmark that matches what it holds.

composites_backfill set Core Balanced (50% equity, 30% fixed income, 10% property, 10%
alternatives) against AGG, a bond index, and Core Growth against SPY alone - so a balanced book
that is mostly equity showed twenty-odd points of "alpha" that was only the stock market. Each
model now gets a blend from its own targets: its defensive share (fixed income, cash) against
AGG, the rest against SPY (performance.blend_for_targets).

Only a benchmark this platform set is replaced - none, "AGG" or "SPY". One a firm chose itself
(any other symbol, or a blend already) is left as it is. A composite reports against its model's
benchmark, so its benchmark changes with it; the ledger records each change. Idempotent.

    python -m seed.benchmark_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.aurea_core.performance import DEFENSIVE_PROXY, GROWTH_PROXY, benchmark_label, blend_for_targets
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.portfolio import Instrument, ModelPortfolio
from app.models.tenant import Firm
from app.provenance import ledger

log = get_logger("aurea.seed.benchmark_backfill")

PLATFORM_DEFAULTS = {None, "", GROWTH_PROXY, DEFENSIVE_PROXY}


async def backfill() -> None:
    async with SessionLocal() as s:
        changed = 0
        for firm in (await s.execute(select(Firm))).scalars().all():
            have = set((await s.execute(select(Instrument.symbol).where(
                Instrument.firm_id == firm.id, Instrument.symbol.in_([GROWTH_PROXY, DEFENSIVE_PROXY])))).scalars().all())
            if have != {GROWTH_PROXY, DEFENSIVE_PROXY}:
                print(f"{firm.slug}: skipped - needs both {GROWTH_PROXY} and {DEFENSIVE_PROXY} priced")
                continue
            models = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
                                      .options(selectinload(ModelPortfolio.targets)))).scalars().all()
            for model in models:
                if model.benchmark_symbol not in PLATFORM_DEFAULTS:
                    continue
                blend = blend_for_targets(model.targets)
                if not blend or blend == model.benchmark_symbol:
                    continue
                before = model.benchmark_symbol
                model.benchmark_symbol = blend
                await ledger.append_entry(
                    s, firm_id=firm.id, event_type="benchmark_changed", actor="system",
                    content={"model": model.name, "from": before, "to": blend,
                             "reason": "Benchmark set from the model's own targets: defensive share "
                                       f"against {DEFENSIVE_PROXY}, the rest against {GROWTH_PROXY}."})
                changed += 1
                print(f"{firm.slug}: {model.name}: {before or 'none'} -> {benchmark_label(blend)}")
        await s.commit()
        log.info("benchmarks_set", changed=changed)
        print(f"\n{changed} model benchmark(s) set.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
