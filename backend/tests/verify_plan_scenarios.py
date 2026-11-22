"""Plan scenarios in the conversation, saved and ranked, and changing no record (B-179).

    python -m tests.verify_plan_scenarios

The demo firm from seed/run.py in a throwaway SQLite file (the model is never called):
  - a scenario needs a lever, and a goal's lever needs the goal; bounds are checked;
  - each lever moves what it should, beside the plan as it stands: retiring later, spending less, moving a goal, a
    smaller target, saving more, and claiming Social Security later (a US household, from its statement);
  - what it gives up is in today's money with its basis, and success points per 10,000 given up rank it;
  - a claim age for a household with no Social Security on file is refused;
  - running writes nothing; saving keeps the scenario and nothing else, on the audit log;
  - saved scenarios read back ranked, and say when they ran on earlier assumptions;
  - the Confirm card names the scenario and its effect, and the conversation reads the ranked list.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_scenarios_"), "scenarios.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import assumptions, financial_plan, scenarios  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ExecutorError, ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Goal, Household, Person  # noqa: E402
from app.models.identity import AuditEvent, User  # noqa: E402
from app.models.financial_plan import IncomeStream  # noqa: E402
from app.models.scenarios import PlanScenario  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro) -> None:
    try:
        await coro
        check(label, "allowed", "refused")
    except scenarios.ScenarioError as e:
        check(label, "refused", "refused")
        print(f"         ({e})")


def part(out: dict, lever: str) -> dict | None:
    return next((p for p in out["given_up"]["parts"] if p["lever"] == lever), None)


async def counts(s) -> tuple:
    return tuple([(await s.execute(select(func.count(m.id)))).scalar() for m in (Goal, IncomeStream, PlanScenario)]
                 + [sorted((g.target_amount, str(g.target_date)) for g in (await s.execute(select(Goal))).scalars().all())])


def registered() -> None:
    """`create_all` makes only the tables `app.models` imports. Checked in a fresh interpreter, because this suite
    imports the model itself and would hide a table the deploy never creates."""
    import subprocess

    code = ("import app.models; from app.core.db import Base; "
            "print(sorted(t for t in ('plan_scenario', 'assumption_set_version') if t in Base.metadata.tables))")
    # A Postgres address that is never connected to: importing builds the engine, and SQLite refuses its pool options.
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://check:check@127.0.0.1:1/check"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).stdout.strip().splitlines()
    print("\n=== The tables a deploy creates ===")
    check("app.models registers the scenario and assumption tables", out[-1] if out else None,
          "['assumption_set_version', 'plan_scenario']")


async def main() -> int:
    registered()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()

    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        hh = (await s.execute(select(Household).join(Goal, Goal.household_id == Household.id)
                              .where(Household.firm_id == firm.id, Household.left_on.is_(None), Household.name.like("%Chen%")))).scalars().first()
        hh = hh or (await s.execute(select(Household).join(Goal, Goal.household_id == Household.id)
                                    .where(Household.firm_id == firm.id, Household.left_on.is_(None)))).scalars().first()
        goal = (await s.execute(select(Goal).where(Goal.household_id == hh.id, Goal.kind != "retirement"))).scalars().first() \
            or (await s.execute(select(Goal).where(Goal.household_id == hh.id))).scalars().first()
        print(f"\n=== {hh.name}; goal {goal.name} ===")

        print("\n=== What a scenario needs ===")
        await refused("a lever", scenarios.run(s, firm, hh.id, {}))
        await refused("a goal's lever needs the goal", scenarios.run(s, firm, hh.id, {"goal_years_delta": 2}))
        await refused("an age in bounds", scenarios.run(s, firm, hh.id, {"retire_at": 99}))
        await refused("an amount above nothing", scenarios.run(s, firm, hh.id, {"spending": -5}))
        await refused("a goal of this household", scenarios.run(s, firm, hh.id, {"goal_id": "nope", "goal_target": 10}))
        await refused("a claim age needs Social Security on file", scenarios.run(s, firm, hh.id, {"claim_age": 70}))
        other = (await s.execute(select(Household).where(Household.firm_id != firm.id))).scalars().first()
        if other is not None:
            await refused("a household of another firm", scenarios.run(s, firm, other.id, {"retire_at": 67}))

        before = await counts(s)
        print("\n=== Each lever ===")
        base = await financial_plan.for_household(s, hh.id, firm_id=firm.id)
        r0 = base["retirement"]
        later = await scenarios.run(s, firm, hh.id, {"retire_at": r0["retirement_age"] + 3})
        check("retiring later lifts retirement success", later["after"]["retirement_success"] >= later["before"]["retirement_success"], True)
        check("the plan as it stands is the plan's", later["before"]["retirement_success"], r0["success_probability"])
        check("it gives up three years of retirement income", part(later, "retire_at")["amount"], round(3 * r0["income_need"]))
        check("points per 10k given up are worked", later["points_per_10k"] is not None, True)
        less = await scenarios.run(s, firm, hh.id, {"spending": round(r0["income_need"] * 0.8)})
        check("spending less lifts retirement success", less["change"]["retirement_points"] >= 0, True)
        check("and gives up spending over retirement", part(less, "spending")["amount"] > 0, True)
        moved = await scenarios.run(s, firm, hh.id, {"goal_id": str(goal.id), "goal_years_delta": 3})
        g = next(x for x in moved["change"]["goals"] if x["goal"] == goal.name)
        check("a goal three years later is likelier", g["after"] >= g["before"], True)
        check("and having it later is valued as given up", part(moved, "goal_years_delta")["amount"] > 0, True)
        smaller = await scenarios.run(s, firm, hh.id, {"goal_id": str(goal.id), "goal_target": float(goal.target_amount) * 0.5})
        g = next(x for x in smaller["change"]["goals"] if x["goal"] == goal.name)
        check("half the target is likelier", g["after"] >= g["before"], True)
        check("and gives up half the target", part(smaller, "goal_target")["amount"], round(float(goal.target_amount) * 0.5))
        more = await scenarios.run(s, firm, hh.id, {"extra_contribution": 20_000})
        check("saving more lifts retirement success", more["change"]["retirement_points"] >= 0, True)
        check("and gives up what is saved", part(more, "extra_contribution")["amount"] > 0, True)
        check("selling down a concentrated holding is named as no lever here", later["not_levers"], ["concentration_unwind"])
        check("running writes nothing", await counts(s), before)

        print("\n=== Claiming Social Security later, a US household ===")
        us = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        patricia = (await s.execute(select(Person).where(Person.household_id == us.id, Person.full_name.like("Patricia%")))).scalars().first()
        await financial_plan.record_stream(s, patricia.id, firm_id=firm.id, by="Test", kind="social_security", name="Social Security",
                                           annual_amount=30_000, start_age=67, source_document="SSA statement, test")
        await s.commit()
        claim = await scenarios.run(s, firm, us.id, {"claim_age": 70})
        p = part(claim, "claim_age")
        check("waiting three years forgoes three years of the benefit", p["amount"], 90_000)
        check("named as forgone while waiting", "forgone while waiting" in p["basis"], True)

        print("\n=== Saving and ranking ===")
        before = await counts(s)
        prep = await scenarios.prepare_save(s, firm, hh.id, name="Retire three years later", levers={"retire_at": r0["retirement_age"] + 3})
        check("a preview writes nothing", await counts(s), before)
        await refused("a saved scenario needs a name", scenarios.prepare_save(s, firm, hh.id, name=" ", levers={"retire_at": 67}))
        saved1 = await scenarios.save(s, firm, adviser, hh.id, name="Retire three years later", levers={"retire_at": r0["retirement_age"] + 3})
        await scenarios.save(s, firm, adviser, hh.id, name="Spend a fifth less", levers={"spending": round(r0["income_need"] * 0.8)})
        await scenarios.save(s, firm, adviser, hh.id, name="Save 20k more", levers={"extra_contribution": 20_000})
        await s.commit()
        after = await counts(s)
        check("saving adds the scenarios and nothing else", (after[0], after[1], after[2], after[3]), (before[0], before[1], before[2] + 3, before[3]))
        check("saving is on the audit log", (await s.execute(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "plan_scenario.saved"))).scalar(), 3)
        check("a saved scenario keeps its result", saved1["points"], prep["result"]["change"]["points"])
        out = await scenarios.saved(s, firm, hh.id)
        ranked = out["scenarios"]
        check("three read back", len(ranked), 3)
        priced = [r["points_per_10k"] for r in ranked if r["points_per_10k"] is not None]
        check("ranked best first by points per 10k given up", priced, sorted(priced, reverse=True))
        check("on the assumptions standing now", {r["earlier_assumptions"] for r in ranked}, {False})

        research = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.RESEARCH_CIO))).scalars().first()
        if research is None:
            research = User(firm_id=firm.id, email="research@scenarios.test", full_name="Rina Research", role=UserRole.RESEARCH_CIO,
                            is_active=True, hashed_password="x")
            s.add(research)
            await s.flush()
        await assumptions.record(s, firm, research, changes=[{"figure": "return.equity", "value": 0.05, "source": "Test outlook"}],
                                 reason="Test", refresh=False)
        await s.commit()
        out = await scenarios.saved(s, firm, hh.id)
        check("after a new assumption version they say so", {r["earlier_assumptions"] for r in out["scenarios"]}, {True})

        print("\n=== The conversation ===")
        check("running is a read", TOOLS["run_plan_scenario"].change_state, ToolChangeState.NO)
        check("saving waits on a Confirm card", TOOLS["save_plan_scenario"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check("an adviser or paraplanner saves", TOOLS["save_plan_scenario"].roles_required, {UserRole.ADVISER, UserRole.PARAPLANNER})
        check("no client holds them", any(UserRole.CLIENT in TOOLS[k].roles_required
                                          for k in ("run_plan_scenario", "save_plan_scenario", "read_plan_scenarios")), False)
        ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
        run = await ex.run_plan_scenario({"household_id": str(hh.id), "retire_at": r0["retirement_age"] + 2})
        check("Astra runs one", run["levers"], {"retire_at": r0["retirement_age"] + 2})
        card = await ex.preview_change("save_plan_scenario", {"household_id": str(hh.id), "name": "Two years later",
                                                              "retire_at": r0["retirement_age"] + 2})
        rows = {r["label"]: r["value"] for r in (card or {}).get("rows", [])}
        check("the card names the scenario", rows.get("Scenario"), "Two years later")
        check("and the change in retirement success", "Retirement success" in rows, True)
        check("and what it gives up", "Given up, today's money" in rows, True)
        check("and that it changes no record", rows.get("After this"), "Kept on the household's scenarios; no record changes")
        try:
            await ex.preview_change("save_plan_scenario", {"household_id": str(hh.id), "name": "x"})
            check("a scenario with no lever is no card", "card", "refused")
        except ExecutorError:
            check("a scenario with no lever is no card", "refused", "refused")
        listed = await ex.read_plan_scenarios({"household_id": str(hh.id)})
        check("Astra reads them ranked", [r["rank"] for r in listed["scenarios"]], [1, 2, 3])

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
