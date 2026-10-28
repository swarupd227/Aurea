"""Give each model portfolio a benchmark that matches what it holds, with history to measure it.

composites_backfill set Core Balanced (50% equity, 30% fixed income, 10% property, 10%
alternatives) against AGG, a bond index, and Core Growth against SPY alone - so a balanced book
that is mostly equity showed twenty-odd points of "alpha" that was only the stock market. Each
model now gets a blend from its own targets: its defensive share (fixed income, cash) against
AGG, the rest against SPY (performance.blend_for_targets).

Only a benchmark this platform set is replaced - none, "AGG" or "SPY". One a firm chose itself
(any other symbol, or a blend already) is left as it is. A composite reports against its model's
benchmark, so its benchmark changes with it; the ledger records each change.

A benchmark is only measurable from its first price. SPY was added later than the book's own
history (a year of monthly closes), so a household measured from before then had no benchmark
at all. The proxies' real monthly closes are fetched back to the firm's earliest real price and
stored as real prices; a date already on file is never touched. Idempotent.

    python -m seed.benchmark_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.aurea_core.performance import DEFENSIVE_PROXY, GROWTH_PROXY, benchmark_label, blend_for_targets
from app.conduit.marketdata import fetch_history_yahoo
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.portfolio import Instrument, ModelPortfolio, Price
from app.models.tenant import Firm
from app.provenance import ledger

log = get_logger("aurea.seed.benchmark_backfill")

PLATFORM_DEFAULTS = {None, "", GROWTH_PROXY, DEFENSIVE_PROXY}
_SNAP_DAYS = 5   # the performance engine matches month-ends this far apart


def _range_back_to(d: date) -> str:
    days = (date.today() - d).days
    return "2y" if days <= 700 else "5y" if days <= 1800 else "10y"


async def _extend_history(s, firm, inst: Instrument, back_to: date) -> int:
    """Real monthly closes for `inst` from `back_to` up to its first stored price."""
    first = (await s.execute(select(func.min(Price.as_of)).where(Price.instrument_id == inst.id))).scalar()
    if first is not None and (first - back_to).days <= _SNAP_DAYS:
        return 0
    added = 0
    for d_iso, close in await fetch_history_yahoo(inst.symbol, rng=_range_back_to(back_to), interval="1mo"):
        d = date.fromisoformat(d_iso)
        if d < back_to.replace(day=1) or d >= date.today() or (first is not None and d >= first):
            continue
        s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=d, close=close,
                    currency=inst.currency or "USD", source="yahoo_history", is_real=True))
        added += 1
    return added


async def backfill() -> None:
    async with SessionLocal() as s:
        changed = 0
        for firm in (await s.execute(select(Firm))).scalars().all():
            proxies = (await s.execute(select(Instrument).where(
                Instrument.firm_id == firm.id, Instrument.symbol.in_([GROWTH_PROXY, DEFENSIVE_PROXY])))).scalars().all()
            if {i.symbol for i in proxies} != {GROWTH_PROXY, DEFENSIVE_PROXY}:
                print(f"{firm.slug}: skipped - needs both {GROWTH_PROXY} and {DEFENSIVE_PROXY} as instruments")
                continue

            earliest = (await s.execute(select(func.min(Price.as_of)).where(
                Price.firm_id == firm.id, Price.is_real.is_(True)))).scalar()
            if earliest is not None:
                for inst in proxies:
                    n = await _extend_history(s, firm, inst, earliest)
                    if n:
                        print(f"{firm.slug}: {inst.symbol}: {n} earlier monthly close(s) added, back to {earliest}")

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
