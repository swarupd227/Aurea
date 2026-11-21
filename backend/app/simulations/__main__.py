"""Seed, discard or report the simulations (see this package's docstring).

    python -m app.simulations status
    python -m app.simulations seed [key ...]
    python -m app.simulations discard [key ...]

Seeding writes each simulation's data onto the firms it names (the demonstration firms) and opts them in; it is safe to
repeat. Discarding removes every row the simulation tagged, on every firm, and its opt-in; it is safe to repeat too.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

import app.models  # noqa: F401
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models.tenant import Firm
from app.simulations import catalogue, enabled_for


async def run(action: str, keys: list[str]) -> int:
    sims = catalogue()
    unknown = [k for k in keys if k not in sims]
    if unknown:
        print(f"No simulation called {', '.join(unknown)}. There are: {', '.join(sims) or 'none'}.")
        return 2
    chosen = [sims[k] for k in (keys or sims)]
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm).order_by(Firm.slug))).scalars().all()
        if action == "status":
            for sim in chosen:
                on = [f.slug for f in firms if enabled_for(f, sim.key)]
                print(f"{sim.key:28} {sim.name:40} seeded on: {', '.join(on) or 'none'}")
                print(f"{'':28} stands for {sim.stands_for}; plugs into {sim.seam}")
            return 0
        for sim in chosen:
            if action == "seed":
                targets = await sim.firms(s) if sim.firms else []
                for firm in targets:
                    out = await sim.seed(s, firm)
                    print(f"seeded {sim.key} on {firm.slug}: {out}")
                if not targets:
                    print(f"{sim.key}: no firm to seed")
            elif action == "discard":
                for firm in firms:
                    out = await sim.discard(s, firm)
                    if any(out.values()) or enabled_for(firm, sim.key):
                        print(f"discarded {sim.key} on {firm.slug}: {out}")
            else:
                print("seed, discard or status")
                return 2
        await s.commit()
    return 0


if __name__ == "__main__":
    configure_logging()
    args = sys.argv[1:] or ["status"]
    sys.exit(asyncio.run(run(args[0], args[1:])))
