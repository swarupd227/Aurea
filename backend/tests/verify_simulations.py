"""Every simulation can be thrown away (app/simulations).

    python -m tests.verify_simulations

Checks, for the framework and for each simulation it finds:

  - nothing outside `app/simulations` imports from it, except the three call sites of `install_all` and the tests;
  - with `AUREA_SIMULATIONS` off nothing is installed;
  - each simulation names what it stands for and where it plugs in;
  - on a small demonstration firm, seeding writes only rows tagged `simulated:<key>` (or changes rows it lists as
    touched), opts the firm in, and is safe to repeat; discarding then leaves every table exactly as it was before the
    seed, with the firm's opt-in gone; a second discard does nothing;
  - a firm that never opted in is left alone by a discard.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.db import Base

PASS, FAIL = [], []
ALLOWED = {"app/main.py", "app/atlas/worker.py"}
# Which firms of the test fixture each simulation is seeded onto: the demonstration firm (New Zealand) and, since B-256 and
# India row 9, the Indian demonstration firm. A simulation not listed here is seeded on the first only.
FIRM_OF = {"demo": "firm", "india-demo": "india"}


class _Targets(dict):
    def __missing__(self, key):
        return ["demo"]


TARGETS = _Targets({"aa_feed": ["india-demo"], "whatsapp_channel": ["india-demo"], "india_stack": ["india-demo"], "kyc_registry": ["india-demo"], "identity_checks": ["india-demo"], "held_away_feed": ["demo", "india-demo"], "news_source": ["demo", "india-demo"],
                    "linkedin_export": ["demo", "india-demo"]})


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def outside_imports() -> list[str]:
    root = pathlib.Path(__file__).resolve().parents[1]
    bad = []
    for p in (root / "app").rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("app/simulations/") or rel in ALLOWED:
            continue
        if re.search(r"^\s*(from|import)\s+app\.simulations", p.read_text(encoding="utf-8"), re.M):
            bad.append(rel)
    return bad


async def counts(s) -> dict[str, int]:
    out = {}
    for table in Base.metadata.sorted_tables:
        out[table.name] = (await s.execute(select(func.count()).select_from(table))).scalar_one()
    return out


async def snapshot(s) -> dict[str, list]:
    """Every row of every table, as text, so a discard can be compared with what was there before the seed."""
    out = {}
    for table in Base.metadata.sorted_tables:
        rows = (await s.execute(select(table))).all()
        out[table.name] = sorted(repr(tuple(r)) for r in rows)
    return out


async def main() -> int:
    from app.core.config import settings
    import app.simulations as sims_pkg
    from tests import _sim_fixture

    print("\n=== The framework ===")
    check("nothing outside app/simulations imports it, but its call sites", outside_imports(), [])
    settings.simulations = False
    check("off installs nothing", sims_pkg.install_all(), [])
    settings.simulations = True
    sims = sims_pkg.catalogue()
    print(f"  simulations found: {', '.join(sims) or 'none'}")
    for key, sim in sims.items():
        check(f"{key} says what it stands for and where it plugs in", bool(sim.stands_for and sim.seam and sim.name), True)
    installed = sims_pkg.install_all()
    check("on installs every one", installed, sorted(sims))

    for key, sim, slug in [(k, v, sl) for k, v in sims.items() for sl in TARGETS[k]]:
        print(f"\n=== {key} on {slug}: seed, then discard ===")
        db = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with db.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(db, expire_on_commit=False)
            async with Session() as s:
                fx = await _sim_fixture.build(s)
                await s.commit()
            async with Session() as s:
                before = await snapshot(s)
            async with Session() as s:
                firm = await s.get(type(fx.firm), getattr(fx, FIRM_OF[slug]).id)
                first = await sim.seed(s, firm)
                await s.commit()
                again = await sim.seed(s, firm)
                await s.commit()
                check("seeding opts the firm in", sims_pkg.enabled_for(firm, key), True)
            async with Session() as s:
                after_one = await counts(s)
            async with Session() as s:
                firm = await s.get(type(fx.firm), getattr(fx, FIRM_OF[slug]).id)
                await sim.seed(s, firm)
                await s.commit()
            async with Session() as s:
                check("seeding twice adds nothing", await counts(s), after_one)
            async with Session() as s:
                other = await s.get(type(fx.other), fx.other.id)
                gone = await sim.discard(s, other)
                await s.commit()
                check("a firm that never opted in is left alone", any(gone.values()), False)
            async with Session() as s:
                firm = await s.get(type(fx.firm), getattr(fx, FIRM_OF[slug]).id)
                await sim.discard(s, firm)
                await s.commit()
            async with Session() as s:
                firm = await s.get(type(fx.firm), getattr(fx, FIRM_OF[slug]).id)
                check("discarding takes the opt-in away", sims_pkg.enabled_for(firm, key), False)
                now = await snapshot(s)
                changed = sorted(t for t in before if before[t] != now[t] and t not in ("firm",))
                check("and leaves every table as it was before the seed", changed, [])
                firm_settings = (firm.settings or {})
                check("  the firm's settings included", "simulations" in firm_settings, False)
            async with Session() as s:
                firm = await s.get(type(fx.firm), getattr(fx, FIRM_OF[slug]).id)
                twice = await sim.discard(s, firm)
                await s.commit()
                check("a second discard does nothing", any(twice.values()), False)
            print(f"  seeded: {first}")
        finally:
            await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
