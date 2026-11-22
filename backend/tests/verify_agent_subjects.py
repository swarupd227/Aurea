"""Every agent runs on the demo firm, and every proposal's subject is a real id (B-226).

    python -m tests.verify_agent_subjects

Builds the demo firm from seed/run.py in a throwaway SQLite file, with the model forced to its written fallback
and no network, then runs every registered agent through `runtime.run_agent`: once across the book and once on a
household. SQLite stores a UUID column as text and will not take a string where the column wants a UUID, so an agent
that hands a household's id over as a string fails here, where Postgres quietly casts it live.

What it checks:
  - no agent raises on the demo firm, across the book or on a household;
  - every proposal any agent raised has a subject id that is a UUID, and it names a row in the firm;
  - `Subject` itself turns an id given as text into a UUID, and refuses one that is not an id.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
import uuid

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_subjects_"), "subjects.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: F401,E402  - registers every table
from app.atlas import registry, runtime  # noqa: E402
from app.atlas.base import Subject  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.llm.service import llm_service  # noqa: E402
from app.models.governance import Recommendation  # noqa: E402
from app.models.graph import Household, Mandate, Person  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


_real_generate = llm_service.generate


async def _fallback_only(**kw):
    kw["force_fallback"] = True
    return await _real_generate(**kw)


async def _no_research(*a, **k):
    return []


async def main() -> int:
    llm_service.generate = _fallback_only
    try:
        from app.aurea_core import knowledge
        knowledge.retrieve = _no_research
    except Exception:  # noqa: BLE001 - no research store in this build
        pass

    print("\n=== Subject takes an id as text ===")
    hid = uuid.uuid4()
    check("a string id becomes a UUID", Subject("household", str(hid), "x").id, hid)
    check("a UUID is kept", Subject("household", hid, "x").id, hid)
    check("no id stays none", Subject("firm").id, None)
    try:
        Subject("household", "not-an-id")
        check("a string that is not an id is refused", "accepted", "refused")
    except ValueError:
        check("a string that is not an id is refused", "refused", "refused")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()

    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        hh = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Chen%")))).scalars().first()
        hh = hh or (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().first()
        from app.aurea_core.graph import household_brain
        print("\n=== The household brain takes an id as text ===")
        brain = await household_brain(s, str(hh.id), firm_id=firm.id)
        check("a household's id as text finds it", (brain or {}).get("household", {}).get("name") if brain else None, hh.name)
        check("text that is no id is not found", await household_brain(s, "not-an-id", firm_id=firm.id), None)
        check("another firm's id is still not found", await household_brain(s, str(hh.id), firm_id=uuid.uuid4()), None)
    print(f"\n=== Every agent on {firm.name} (household {hh.name}) ===")

    failures: list[str] = []
    for key in sorted(registry.all_agents(), key=lambda k: k.value):
        for where, subject in (("the book", None), ("a household", Subject("household", hh.id, hh.name))):
            async with SessionLocal() as s:
                f = await s.get(Firm, firm.id)
                try:
                    await runtime.run_agent(s, firm=f, agent_key=key, subject=subject, trigger="manual")
                    await s.commit()
                except runtime.AgentPausedError:
                    await s.rollback()
                except Exception as e:  # noqa: BLE001 - every failure is reported, then the next agent runs
                    await s.rollback()
                    tail = traceback.format_exception(e)[-1].strip()[:220]
                    failures.append(f"{key.value} on {where}: {tail}")
    for line in failures:
        print(f"    {line}")
    check("agents that raised", len(failures), 0)

    async with SessionLocal() as s:
        recs = (await s.execute(select(Recommendation).where(Recommendation.firm_id == firm.id))).scalars().all()
        ids = {
            "household": {r for (r,) in (await s.execute(select(Household.id).where(Household.firm_id == firm.id))).all()},
            "mandate": {r for (r,) in (await s.execute(select(Mandate.id).where(Mandate.firm_id == firm.id))).all()},
            "person": {r for (r,) in (await s.execute(select(Person.id).where(Person.firm_id == firm.id))).all()},
        }
    with_subject = [r for r in recs if r.subject_id is not None]
    print(f"\n=== Proposals: {len(recs)}, {len(with_subject)} with a subject id ===")
    check("proposals were raised", len(recs) > 0, True)
    check("every subject id is a UUID", [r.agent_key for r in with_subject if not isinstance(r.subject_id, uuid.UUID)], [])
    stray = [f"{r.agent_key}:{r.subject_type}" for r in with_subject
             if r.subject_type in ids and r.subject_id not in ids[r.subject_type]]
    check("every household, mandate or person subject is one in the firm", stray, [])

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
