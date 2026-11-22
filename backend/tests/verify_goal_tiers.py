"""Goal tiers (B-176, L200-2 §2.3, §4.1).

    .venv\\Scripts\\python.exe -m tests.verify_goal_tiers

A person records each goal as a need, a want or a wish (update_goal, behind its Confirm card). The household's
success is every needs goal met together, in the same simulated future, which is never better than its weakest need;
the result names the first tier, most important first, that is not on track; a goal with no tier on file is "not
tiered", never read as a need. Tiers change no goal's own likelihood and no funding order: priority still decides that.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_view, firm_time, goal_tradeoff
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
ALLOC = {"equity": 600_000.0, "fixed_income": 400_000.0}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def engine() -> None:
    print("\n=== The household's success is every needs goal met together ===")
    goals = [
        {"id": "home", "name": "Home deposit", "priority": 1, "target_amount": 300_000, "years": 5, "tier": "needs"},
        {"id": "fees", "name": "School fees", "priority": 2, "target_amount": 250_000, "years": 8, "tier": "needs"},
        {"id": "boat", "name": "Boat", "priority": 3, "target_amount": 400_000, "years": 10, "tier": "wishes"},
        {"id": "trip", "name": "Trip", "priority": 4, "target_amount": 50_000, "years": 3},
    ]
    # A book that meets each need some of the time, so meeting both together is a real question.
    r = goal_tradeoff.run_tradeoff(goals=goals, total_portfolio=420_000, allocation=ALLOC)
    t = r["tiers"]
    by = {g["id"]: g for g in r["goals"]}
    print(f"     home {by['home']['success_probability']}, fees {by['fees']['success_probability']}, needs together {t['success']}")
    check("each need's own likelihood is reported as before", (by["home"]["tier"], by["fees"]["tier"]), ("needs", "needs"))
    check("each need is met some of the time, not always", all(0 < by[i]["success_probability"] < 1 for i in ("home", "fees")), True)
    check("both needs met together is never better than the weaker one",
          t["success"] <= min(by["home"]["success_probability"], by["fees"]["success_probability"]), True)
    check("the success figure is the needs tier's, and says so", (t["success"], t["success_basis"]), (t["by_tier"]["needs"]["all_met"], "every needs goal met"))
    check("a goal with no tier is listed as not tiered, never read as a need", (t["not_tiered"], "trip" in [g for g in ("home", "fees")]), (["Trip"], False))
    check("a tier with goals short of on track names them", t["by_tier"]["wishes"]["short"], [] if by["boat"]["on_track"] else ["Boat"])
    failed = next((x for x in ("needs", "wants", "wishes") if x in t["by_tier"] and not t["by_tier"][x]["on_track"]), None)
    check("the failed tier is the first, most important first, not on track", t["failed_tier"], failed)

    plain = goal_tradeoff.run_tradeoff(goals=[{k: v for k, v in g.items() if k != "tier"} for g in goals],
                                       total_portfolio=420_000, allocation=ALLOC)
    check("tiers change no goal's own likelihood: priority still funds them",
          [g["success_probability"] for g in plain["goals"]], [g["success_probability"] for g in r["goals"]])
    check("with no goal tiered there is no household success figure", (plain["tiers"]["success"], plain["tiers"]["success_basis"]), (None, "not tiered"))
    wishes_only = goal_tradeoff.run_tradeoff(goals=[{**goals[2]}], total_portfolio=1_000_000, allocation=ALLOC)["tiers"]
    check("tiers with no need give no success figure, and say why", (wishes_only["success"], wishes_only["success_basis"]),
          (None, "no goal is tiered as a need"))
    rich = goal_tradeoff.run_tradeoff(goals=goals[:2], total_portfolio=5_000_000, allocation=ALLOC)["tiers"]
    check("needs a large book meets are on track, and nothing failed", (rich["by_tier"]["needs"]["on_track"], rich["failed_tier"]), (True, None))
    poor = goal_tradeoff.run_tradeoff(goals=goals[:2], total_portfolio=50_000, allocation=ALLOC)["tiers"]
    check("needs a small book cannot meet are the failed tier, each named", (poor["failed_tier"], sorted(poor["by_tier"]["needs"]["short"])),
          ("needs", ["Home deposit", "School fees"]))


async def recorded() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            us = Firm(id=uuid.uuid4(), name="U", slug="u", base_currency="USD", jurisdiction="US", settings={})
            s.add(us)
            await s.flush()
            today = firm_time.today(us)
            adviser = User(id=uuid.uuid4(), firm_id=us.id, email="a@u", full_name="Ada Adviser", role=UserRole.ADVISER, hashed_password="x")
            ops = User(id=uuid.uuid4(), firm_id=us.id, email="o@u", full_name="Otto Ops", role=UserRole.OPERATIONS, hashed_password="x")
            h = Household(id=uuid.uuid4(), firm_id=us.id, name="Lee")
            s.add_all([adviser, ops, h])
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, full_name="Ann Lee", date_of_birth=date(today.year - 50, 1, 1))
            s.add(ann)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=us.id, person_id=ann.id, name="M", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            acct = Account(id=uuid.uuid4(), firm_id=us.id, mandate_id=m.id, name="A", currency="USD", cash_balance=0)
            inst = Instrument(id=uuid.uuid4(), firm_id=us.id, symbol="XLEE", name="X", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add_all([acct, inst])
            await s.flush()
            s.add(Price(firm_id=us.id, instrument_id=inst.id, as_of=today, close=100, currency="USD", source="test", is_real=True))
            s.add(Holding(firm_id=us.id, account_id=acct.id, instrument_id=inst.id, quantity=10_000, market_value=1_000_000, cost_basis=1_000_000))
            home = Goal(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, name="Home deposit", kind="other", target_amount=300_000,
                        target_date=date(today.year + 5, 12, 31), priority=1, assumptions={"note": "kept"})
            boat = Goal(id=uuid.uuid4(), firm_id=us.id, household_id=h.id, name="Boat", kind="other", target_amount=200_000,
                        target_date=date(today.year + 8, 12, 31), priority=3, assumptions={})
            s.add_all([home, boat])
            await s.flush()
            ex = ToolExecutors(s, adviser.id, adviser.role, us.id)

            async def card(inputs):
                return {r["label"]: r["value"] for r in (await ex.preview_change("update_goal", inputs))["rows"]}

            print("\n=== An adviser records a goal's tier, behind its Confirm card ===")
            before = await goal_tradeoff.for_household(s, h.id, firm_id=us.id)
            check("with nothing tiered, the goals page says so", (before["tiers"]["success"], before["tiers"]["not_tiered"]), (None, ["Home deposit", "Boat"]))
            shown = await card({"goal_id": str(home.id), "tier": "needs"})
            check("the card shows the tier before and after", shown.get("Tier"), "Not tiered becomes Needs")
            await refused("an unknown tier", ex.preview_change("update_goal", {"goal_id": str(home.id), "tier": "musts"}), "tier must be needs, wants or wishes")
            done = await ex.update_goal({"goal_id": str(home.id), "tier": "need"})
            await s.refresh(home)
            check("written in the goal's assumptions, nothing else on it changed", home.assumptions, {"note": "kept", "tier": "needs"})
            check("the result carries the goal's tier and the household's tiers", (done["goal"]["tier"], done["tiers"]["success_basis"]),
                  ("needs", "every needs goal met"))
            check("and the message says it", "Tiered as needs" in done["message"], True)
            await refused("naming the tier on file is no change", ex.preview_change("update_goal", {"goal_id": str(home.id), "tier": "needs"}), "already tiered as needs")
            await ex.update_goal({"goal_id": str(boat.id), "tier": "wishes"})
            after = await goal_tradeoff.for_household(s, h.id, firm_id=us.id)
            same = {g["id"]: g["success_probability"] for g in before["goals"]} == {g["id"]: g["success_probability"] for g in after["goals"]}
            check("tiering moved no goal's likelihood", same, True)
            check("the household's success is now its one need's",
                  after["tiers"]["success"], next(g["success_probability"] for g in after["goals"] if g["name"] == "Home deposit"))
            clear = await card({"goal_id": str(boat.id), "tier": "none"})
            check("none clears a tier, and the card says so", clear.get("Tier"), "Wishes becomes Not tiered")
            await ex.update_goal({"goal_id": str(boat.id), "tier": "none"})
            await s.refresh(boat)
            check("cleared from the goal's assumptions", "tier" in (boat.assumptions or {}), False)

            print("\n=== The client's view reads the same ===")
            brain = await __import__("app.aurea_core.graph", fromlist=["household_brain"]).household_brain(s, h.id, firm_id=us.id)
            mine = client_view.goals(brain, today)
            check("each goal carries its tier and the household its tiers", ([g["tier"] for g in mine["goals"]], mine["tiers"]["success_basis"]),
                  (["needs", None], "every needs goal met"))
            # Rights are the catalogue's, which the gateway enforces before any executor runs.
            from app.core.tools import tools_for_role
            check("an adviser and a paraplanner may tier a goal; operations and a client may not",
                  ["update_goal" in tools_for_role(r) for r in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.CLIENT)],
                  [True, True, False, False])
    finally:
        await db.dispose()


def main() -> int:
    engine()
    asyncio.run(recorded())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
