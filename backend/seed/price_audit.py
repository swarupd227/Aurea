"""Find price history that is labelled real but was invented, and say so.

`seed/run.py` used to store its offline fallback - a deterministic straight-line path - as
real Yahoo history (`is_real=True`). Anything computed from it (returns, composites, bills)
would then present an invented series as market data. Real prices essentially never move in
a perfectly constant step month after month, so a history whose successive differences are
all equal is not a market's.

    python -m seed.price_audit            # report only
    python -m seed.price_audit --relabel  # mark what it finds as synthetic

Relabelling does not delete anything: the rows stay, marked `synthetic_history` and not real,
so every consumer that already names synthetic prices will name these. The next price sync
then replaces them with real closes for any date the feed returns.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models.portfolio import Instrument, Price

MIN_POINTS = 6          # fewer points than this proves nothing
REL_TOLERANCE = 1e-3    # successive differences equal within 0.1% of the step


def looks_invented(closes: list[float]) -> bool:
    if len(closes) < MIN_POINTS:
        return False
    steps = [b - a for a, b in zip(closes, closes[1:])]
    first = steps[0]
    if abs(first) < 1e-9:
        return all(abs(s) < 1e-9 for s in steps)  # a flat line is not a market either
    return all(abs(s - first) <= abs(first) * REL_TOLERANCE for s in steps)


async def audit(relabel: bool = False) -> list[dict]:
    found: list[dict] = []
    async with SessionLocal() as s:
        for inst in (await s.execute(select(Instrument))).scalars().all():
            rows = (await s.execute(
                select(Price).where(Price.instrument_id == inst.id, Price.is_real.is_(True),
                                    Price.source == "yahoo_history").order_by(Price.as_of)
            )).scalars().all()
            if looks_invented([float(r.close) for r in rows]):
                found.append({"symbol": inst.symbol, "rows": len(rows),
                              "first": rows[0].as_of.isoformat(), "last": rows[-1].as_of.isoformat()})
                if relabel:
                    for r in rows:
                        r.is_real, r.source = False, "synthetic_history"
        if relabel:
            await s.commit()
    return found


if __name__ == "__main__":
    configure_logging()
    relabel = "--relabel" in sys.argv
    hits = asyncio.run(audit(relabel))
    if not hits:
        print("\nNo price history is labelled real and looks invented.\n")
    else:
        verb = "Relabelled as synthetic" if relabel else "Labelled real but look invented (re-run with --relabel)"
        print(f"\n{verb}:")
        for h in hits:
            print(f"  {h['symbol']:<10} {h['rows']} rows, {h['first']} to {h['last']}")
        print()
