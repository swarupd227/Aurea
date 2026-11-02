"""Prove a name that matches nothing gets an offer and not a dead end: the closest names on file, this
firm's only, none of them used until the person says which they meant.

Runs against a throwaway SQLite database.

    python -m tests.verify_client_search
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_names
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm import follow_ups
from app.llm.executors import ToolExecutors
from app.models.enums import EntityType, UserRole
from app.models.graph import Household, LegalEntity, Person
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    print("\n=== How close a name is ===")
    check("a slip in one letter is near", client_names.likeness("Hurtley", "The Hartley Family") >= client_names.NEAR, True)
    check("an extra letter", client_names.likeness("Heartley family", "The Hartley Family") >= client_names.NEAR, True)
    check("a plural", client_names.likeness("the Hartleys", "The Hartley Family") >= client_names.NEAR, True)
    check("a different name is not", client_names.likeness("Hutton", "The Hartley Family") >= client_names.NEAR, False)
    check("sharing the word family is not", client_names.likeness("Smith Family", "The Hartley Family") >= client_names.NEAR, False)
    check("a word that says nothing finds nothing", client_names.closest("the family", [(1, "The Hartley Family")]), [])
    check("closest first", [k for k, _ in client_names.closest("Chenn", [(1, "Chen"), (2, "Chenna")])], [2, 1])
    check("at most three", len(client_names.closest("Lee", [(i, "Lee") for i in range(5)])), 3)

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            mine = Firm(id=uuid.uuid4(), name="M", slug="m", base_currency="NZD", jurisdiction="NZ")
            theirs = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            s.add_all([mine, theirs])
            await s.flush()
            hartley = Household(id=uuid.uuid4(), firm_id=mine.id, name="The Hartley Family")
            chen = Household(id=uuid.uuid4(), firm_id=mine.id, name="The Chen Family")
            morrison = Household(id=uuid.uuid4(), firm_id=mine.id, name="The Morrison Family")
            hurley = Household(id=uuid.uuid4(), firm_id=theirs.id, name="The Hurley Family")
            s.add_all([hartley, chen, morrison, hurley])
            await s.flush()
            s.add_all([Person(firm_id=mine.id, household_id=hartley.id, full_name="James Hartley"),
                       Person(firm_id=mine.id, household_id=chen.id, full_name="Mei Lin"),
                       LegalEntity(firm_id=mine.id, household_id=morrison.id, name="Sokolov Trust", entity_type=EntityType.TRUST),
                       Person(firm_id=theirs.id, household_id=hurley.id, full_name="Mei Linn")])
            await s.flush()
            ex = ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, mine.id)

            async def search(query):
                return await ex.search_households({"query": query})

            print("\n=== A name that matches ===")
            r = await search("Hartley")
            check("a household by its name", [h["name"] for h in r["results"]], ["The Hartley Family"])
            check("nothing else is offered", "did_you_mean" in r, False)
            check("a household by a person in it", [h["name"] for h in (await search("James Hartley"))["results"]], ["The Hartley Family"])
            check("a household by an entity in it", [h["name"] for h in (await search("Sokolov"))["results"]], ["The Morrison Family"])
            check("another firm's person finds nothing", (await search("Mei Linn"))["results"], [])

            print("\n=== A name that does not ===")
            r = await search("Hurtley")
            check("nothing is treated as found", r["results"], [])
            check("the closest name is offered", [h["name"] for h in r["did_you_mean"]], ["The Hartley Family"])
            check("with its id, for the turn that follows", r["did_you_mean"][0]["household_id"], str(hartley.id))
            check("the model is told to ask first", "ask which was meant" in r["message"], True)
            check("and the name is said", "The Hartley Family" in r["message"], True)
            check("another firm's client is never offered", "The Hurley Family" in str(r), False)
            r = await search("Mei Lynn")
            check("a person's name reaches their household", [(h["name"], h.get("matched")) for h in r["did_you_mean"]], [("The Chen Family", "Mei Lin")])
            r = await search("Sokolof")
            check("and an entity's", [(h["name"], h.get("matched")) for h in r["did_you_mean"]], [("The Morrison Family", "Sokolov Trust")])
            r = await search("Zzyzx")
            check("nothing close says so", (r["results"], r["did_you_mean"], "no name on file" in r["message"]), ([], [], True))

            print("\n=== What the person can press ===")
            used = [{"tool_key": "search_households", "inputs": {"query": "Hurtley"}, "result": await search("Hurtley")}]
            check("each near name is one press away", follow_ups.derive(UserRole.ADVISER, used)[0], "I meant The Hartley Family.")
            out = follow_ups.derive(UserRole.ADVISER, [{"tool_key": "search_households", "inputs": {}, "result": await search("Hartley")}])
            check("a real match still leads to its next steps", any("the Hartley Family" in x for x in out), True)
            check("the tool says not to use a near name", "do not read or change anything" in TOOLS["search_households"].description, True)
            check("a client holds no search", UserRole.CLIENT in TOOLS["search_households"].roles_required, False)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
