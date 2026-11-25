"""Prove the live India proof harness (app/tools_live/india_live_proof.py) passes on the demonstration seed and can write nothing.

Builds the Indian firm exactly as verify_india_demo does (seed/india_demo.py, invented prices, no network), commits it, then
runs the harness on a fresh session as the container would, and expects every check to PASS. It then proves the guards on that
database: a section that tries to write is stopped and reported, a SELECT-only run leaves every table's row count where it was,
and the harness itself added nothing.

    python -m tests.verify_india_live_proof
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.models.engagement import Task
from app.models.graph import Household
from app.tools_live import india_live_proof as proof
from seed import india_demo

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def counts(session) -> dict[str, int]:
    out = {}
    for t in Base.metadata.sorted_tables:
        out[t.name] = (await session.execute(text(f'select count(*) from "{t.name}"'))).scalar_one()
    return out


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            await india_demo.build(s)
            await s.commit()

        print("\n=== The harness on the seeded firm ===")
        async with Session() as s:
            before = await counts(s)
            lines: list[str] = []
            P = await proof.run(s, out=lambda line: (lines.append(line), print(line))[1])
            await s.rollback()
        fails = [l for l in lines if l.startswith("FAIL")]
        check("every check passes", fails, [])
        check("the proof ran a real number of checks", P.passed >= 120, True)
        check("the slug the harness reads is the seed's", proof.SLUG, india_demo.SLUG)

        print("\n=== It wrote nothing ===")
        async with Session() as s:
            after = await counts(s)
        check("every table has the rows it had", {k: v for k, v in after.items() if v != before[k]}, {})

        print("\n=== The guards, on this database ===")
        async with Session() as s:
            P2 = proof.Proof(s, out=lambda line: None)
            remove = proof.install_guards(s)
            try:
                firm_found = await proof.load(P2)
                check("the firm is found under the guards", firm_found, True)
                n_tasks = (await s.execute(select(func.count()).select_from(Task))).scalar_one()

                async with P2.section("a read that adds a row", "test"):
                    s.sync_session.add(Task(firm_id=P2.firm.id, title="should never exist"))     # past add()'s own refusal
                    await s.flush()
                check("a flush with a new row is stopped and reported as a FAIL", (P2.failed, P2.passed > 0), (1, True))

                async with P2.section("a read that changes a row", "test"):
                    h = next(iter(P2.houses.values()))
                    h.name = h.name + " (edited)"
                    await s.flush()
                check("a flush with a changed row is stopped and reported as a FAIL", P2.failed, 2)
                await s.rollback()

                async with P2.section("a read that deletes a row", "test"):
                    await s.delete(next(iter(P2.houses.values())))
                check("a delete is stopped and reported", P2.failed, 3)

                async with P2.section("a read that commits", "test"):
                    await s.commit()
                check("a commit is stopped and reported", P2.failed, 4)

                async with P2.section("a read that sends an UPDATE", "test"):
                    await s.execute(text(f"update {Household.__tablename__} set name = 'x'"))
                check("a raw UPDATE is stopped at the cursor and reported", P2.failed, 5)
                await s.rollback()
                check("a SELECT still runs under the guards", (await s.execute(select(func.count()).select_from(Task))).scalar_one(), n_tasks)
                check("and the household names are as they were", [h for h in (await s.execute(select(Household.name))).scalars().all() if "edited" in h or h == "x"], [])
            finally:
                remove()
        async with Session() as s:
            check("the guard tests left the database as it was", await counts(s), before)

        print("\n=== The guards' own self-test ===")
        results = await proof.guard_selftest()
        check("each guard stops what it should, and a SELECT runs", [ok for ok, _ in results], [True] * len(results))
        check("there are six of them", len(results), 6)

        print("\n=== A database with no demonstration firm ===")
        empty_db = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with empty_db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(empty_db, expire_on_commit=False)() as s:
            P3 = await proof.run(s, out=lambda line: None, selftest=False)
        await empty_db.dispose()
        check("it fails, saying the firm is missing, and runs nothing else", (P3.failed, P3.passed), (1, 0))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
