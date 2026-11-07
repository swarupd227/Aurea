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
from app.models.crm import CrmContact, CrmOpportunity
from app.models.enums import EntityType, UserRole
from app.models.graph import Household, LegalEntity, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase
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

            print("\n=== Not a client yet (B-160) ===")

            def user(name, role):
                return User(id=uuid.uuid4(), firm_id=mine.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=role, is_active=True)
            sophie, ben, carla = user("Sophie Tran", UserRole.ADVISER), user("Ben Ade", UserRole.ADVISER), user("Carla Ruiz", UserRole.COMPLIANCE)
            pt, cio = user("Pat Team", UserRole.PORTFOLIO_TEAM), user("Rua Cio", UserRole.RESEARCH_CIO)
            s.add_all([sophie, ben, carla, pt, cio, Household(id=uuid.uuid4(), firm_id=mine.id, name="Aroha Whanau Trust")])
            rohan_case = OnboardingCase(id=uuid.uuid4(), firm_id=mine.id, prospect_name="Rohan Mehta", registration_type="individual")
            tane_case = OnboardingCase(id=uuid.uuid4(), firm_id=mine.id, prospect_name="Tane Wiremu", registration_type="individual")
            far_case = OnboardingCase(id=uuid.uuid4(), firm_id=theirs.id, prospect_name="Rohan Mehta", registration_type="individual")
            s.add_all([rohan_case, tane_case, far_case])
            await s.flush()
            rohan = CrmContact(id=uuid.uuid4(), firm_id=mine.id, full_name="Rohan Mehta", contact_type="prospect", owner_user_id=sophie.id)
            priya = CrmContact(id=uuid.uuid4(), firm_id=mine.id, full_name="Priya Raman", contact_type="lead", owner_user_id=ben.id)
            far_rohan = CrmContact(id=uuid.uuid4(), firm_id=theirs.id, full_name="Rohan Mehta", contact_type="prospect")
            s.add_all([rohan, priya, far_rohan])
            await s.flush()
            s.add(CrmOpportunity(firm_id=mine.id, contact_id=rohan.id, title="Rohan Mehta", stage="won", onboarding_case_id=rohan_case.id))
            await s.flush()

            def as_(u):
                return ToolExecutors(s, u.id, u.role, mine.id)

            r = await as_(sophie).search_households({"query": "Rohan Mehta"})
            check("no household is treated as found", r["results"], [])
            check("the adviser's own prospect comes first",
                  [(p["name"], p["stage"], p["owner"], p["onboarding_case_id"]) for p in r["prospects"]],
                  [("Rohan Mehta", "won", "Sophie Tran", str(rohan_case.id))])
            check("with the contact id, for a brief", r["prospects"][0]["contact_id"], str(rohan.id))
            check("and their open case", [c["case_id"] for c in r["onboarding_cases"]], [str(rohan_case.id)])
            check("no household that only sounds alike is offered", ("did_you_mean" in r, "Aroha" in str(r)), (False, False))
            check("the message says not a client yet, and to offer no household",
                  ("not a client yet" in r["message"], "offer no household" in r["message"], "Sophie Tran" in r["message"]), (True, True, True))
            check("the case is not said twice", r["message"].count("onboarding case ("), 0)
            check("a typo still reaches the prospect",
                  [p["name"] for p in (await as_(sophie).search_households({"query": "Rohan Mehtaa"}))["prospects"]], ["Rohan Mehta"])
            r = await as_(sophie).search_households({"query": "Priya Raman"})
            check("an adviser does not see another adviser's contact", ("prospects" in r, "Priya" in str(r.get("did_you_mean"))), (False, False))
            r = await as_(pt).search_households({"query": "Priya Raman"})
            check("the portfolio team reads the firm's pipeline", [(p["name"], p["owner"]) for p in r["prospects"]], [("Priya Raman", "Ben Ade")])
            r = await as_(carla).search_households({"query": "Tane Wiremu"})
            check("compliance holds no pipeline but sees an open case",
                  (r["prospects"], [c["prospect_name"] for c in r["onboarding_cases"]]), ([], ["Tane Wiremu"]))
            r = await as_(cio).search_households({"query": "Rohan Mehta"})
            check("research reads the pipeline and not the cases", ([p["name"] for p in r["prospects"]], r["onboarding_cases"]), (["Rohan Mehta"], []))
            leaked = (str(far_rohan.id) in str(await as_(pt).search_households({"query": "Rohan Mehta"}))
                      or str(far_case.id) in str(await as_(carla).search_households({"query": "Rohan Mehta"})))
            check("another firm's prospect or case is never found", leaked, False)
            tane_case.status = "approved"
            await s.flush()
            check("a closed case is not offered", "onboarding_cases" in await as_(carla).search_households({"query": "Tane Wiremu"}), False)
            check("the tool says prospects come first", "someone not yet a client comes first" in TOOLS["search_households"].description, True)

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
