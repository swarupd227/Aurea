"""Simulations: stand-ins for systems and data Astra does not have yet, built to be thrown away.

A simulation stands in for something a client has and we do not: their wealth platform, an aggregation feed, a news
service, an RM's LinkedIn export. The feature it feeds is real and stays; only the source is simulated. When the real
source arrives, the simulation is discarded and nothing else changes. The rules that make that true:

1.  Everything a simulation is lives in its own package here, `app/simulations/<key>/`: its provider, its fixture, its
    seed and its discard. Nothing outside `app/simulations` imports from it.
2.  It plugs into a seam the real feature exposes - a registry the real source will use too (`foundation.adapters
    .register`, and the seams the held-away, news and network features declare). `install_all()` is the one call that
    connects them, made from `app.main`, the worker and the test harness, and only when `settings.simulations` is on.
3.  It runs only for a firm that opted in: `firm.settings["simulations"]` lists the simulations seeded there. Seeding
    adds the key, discarding takes it away. A firm that never opted in never sees one.
4.  Every row it writes is tagged so discarding finds it: `source_system` (or a JSON `source`) of `simulated:<key>`,
    `TAG` below. Nothing it writes is untagged.
5.  What it supplies says so: its provider carries `simulated = True`, the data it returns carries `simulated: true`,
    and every card and page shows a "simulated" chip from that flag.

To discard every simulation: `python -m app.simulations discard`, then set `AUREA_SIMULATIONS=off` (or delete this
package, `seed/simulations/` if any, and the three `install_all` call sites). `tests.verify_simulations` proves each of
these: no import from outside, nothing installed when off, nothing left after a discard, every core suite unchanged.

    python -m app.simulations status            # what is installed and seeded, firm by firm
    python -m app.simulations seed [key ...]    # seed onto the demonstration firms
    python -m app.simulations discard [key ...] # remove what they wrote, and their opt-in
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

TAG_PREFIX = "simulated:"


def tag(key: str) -> str:
    """The source every row a simulation writes carries."""
    return f"{TAG_PREFIX}{key}"


def is_simulated(source: str | None) -> bool:
    return bool(source) and str(source).startswith(TAG_PREFIX)


def enabled_for(firm, key: str) -> bool:
    """Whether a firm opted in to this simulation. Read by the simulation itself, never by core code."""
    return key in ((getattr(firm, "settings", None) or {}).get("simulations") or [])


@dataclass
class Simulation:
    key: str
    name: str
    stands_for: str                 # the real system or data it stands in for
    seam: str                       # where it plugs in, in the real feature's terms
    install: Callable[[], None]     # register its provider at the seam; no database work
    seed: Callable[..., Awaitable[dict]]      # (session, firm) -> what it wrote
    discard: Callable[..., Awaitable[dict]]   # (session, firm) -> what it removed
    firms: Callable[..., Awaitable[list]] = None   # (session) -> the firms it seeds by default
    notes: list[str] = field(default_factory=list)


SIMULATIONS: dict[str, Simulation] = {}
_INSTALLED: set[str] = set()


def register(sim: Simulation) -> None:
    SIMULATIONS[sim.key] = sim


def catalogue() -> dict[str, Simulation]:
    """Every simulation built, imported here so a new one is one line in `_modules`."""
    from importlib import import_module

    for mod in _modules():
        import_module(f"app.simulations.{mod}")
    return SIMULATIONS


def _modules() -> list[str]:
    """The simulation packages, by folder: adding one is adding its folder; discarding one is deleting it."""
    import pathlib

    here = pathlib.Path(__file__).parent
    return sorted(p.name for p in here.iterdir() if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith("_"))


def install_all() -> list[str]:
    """Connect every simulation to its seam. Called only when `settings.simulations` is on; idempotent."""
    from app.core.config import settings

    if not settings.simulations:
        return []
    for key, sim in catalogue().items():
        if key not in _INSTALLED:
            sim.install()
            _INSTALLED.add(key)
    return sorted(_INSTALLED)


def installed() -> list[str]:
    return sorted(_INSTALLED)


async def opt_in(session, firm, key: str) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    s = dict(firm.settings or {})
    keys = list(s.get("simulations") or [])
    if key not in keys:
        keys.append(key)
    s["simulations"] = keys
    firm.settings = s
    flag_modified(firm, "settings")
    await session.flush()


async def opt_out(session, firm, key: str) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    s = dict(firm.settings or {})
    keys = [k for k in (s.get("simulations") or []) if k != key]
    if keys:
        s["simulations"] = keys
    else:
        s.pop("simulations", None)
    firm.settings = s
    flag_modified(firm, "settings")
    await session.flush()
