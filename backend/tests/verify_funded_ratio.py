"""How well funded the needs are, and how bad a failure is, beside each likelihood (B-177).

    python -m tests.verify_funded_ratio

The engines on their own first (no database), then the demo firm from seed/run.py in a throwaway SQLite file:
  - a goal reports the median gap on the paths that fall short, and none when no path does;
  - a retirement plan reports what is left unpaid on the paths that run out, in today's money, and when they run out;
  - the band: below the target, within it, or above the ceiling ("possibly over-saving"), the ceiling the platform's
    95% until a firm records its own;
  - the funded ratio is the managed book over goals and retirement spending discounted at the assumption set's rate,
    the cash return in the same set unless the firm records a rate, never counting a retirement goal twice, and every
    goal counted, said so, while no tiers are on file;
  - a firm's own discount rate and ceiling, recorded in the assumption set, move the ratio and the band;
  - the financial plan and the conversation carry all of it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_funded_"), "funded.db")
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

import app.models  # noqa: F401,E402
from app.aurea_core import assumptions, financial_plan, funded, goal_tradeoff, planning, retirement  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Goal, Household  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def engines() -> None:
    print("\n=== A goal's failure ===")
    alloc = {"equity": 0.6, "fixed_income": 0.4}
    out = goal_tradeoff.run_tradeoff(goals=[{"id": "a", "name": "Stretch", "target_amount": 2_000_000, "years": 10, "priority": 1},
                                            {"id": "b", "name": "Easy", "target_amount": 10_000, "years": 5, "priority": 2}],
                                     total_portfolio=1_000_000, allocation=alloc)
    stretch, easy = out["goals"]
    check("a goal that often falls short reports the median gap", stretch["failed_shortfall"] is not None and stretch["failed_shortfall"] > 0, True)
    check("and the gap is below the target", stretch["failed_shortfall"] < 2_000_000, True)
    check("a goal no path misses reports none", easy["failed_shortfall"], None)

    print("\n=== The band ===")
    check("below the target", planning.band(0.6, 0.7, 0.95), "below")
    check("within it", planning.band(0.8, 0.7, 0.95), "within")
    check("above the ceiling", planning.band(0.99, 0.7, 0.95), "above")
    check("no likelihood, no band", planning.band(None, 0.7, 0.95), None)
    aset = planning.assumption_set({}, "NZ")
    check("the ceiling is the platform's 95%", (aset["success_ceiling"], aset["sources"]["success_ceiling"]), (0.95, "platform"))
    check("the rate is the cash return in the same set", (aset["discount_rate"], aset["sources"]["discount_rate"]), (0.025, "cash_return"))
    mine = planning.assumption_set({"cmas": {"cash": [0.04, 0.01]}}, "NZ")
    check("the cash return the firm set is the rate", mine["discount_rate"], 0.04)
    own = planning.assumption_set({"planning_discount_rate": 0.03, "success_ceiling": 0.9}, "NZ")
    check("a firm's own rate and ceiling stand", (own["discount_rate"], own["sources"]["discount_rate"], own["success_ceiling"]), (0.03, "firm", 0.9))

    print("\n=== A retirement's failure ===")
    poor = retirement.retirement_plan(current_value=300_000, allocation=alloc, current_age=60, retirement_age=65, longevity_age=95,
                                      annual_income=60_000, discount_rate=0.025, success_ceiling=0.95)
    check("a plan that runs out reports what is left unpaid", poor["failed_shortfall_today"] is not None and poor["failed_shortfall_today"] > 0, True)
    check("with the median age it runs out", poor["median_depletion_age"] is not None, True)
    check("and reads below the band", poor["band"], "below")
    check("what it must pay, discounted", poor["pv_spending"] > 0, True)
    rich = retirement.retirement_plan(current_value=20_000_000, allocation=alloc, current_age=60, retirement_age=65, longevity_age=95,
                                      annual_income=60_000, discount_rate=0.025, success_ceiling=0.95)
    check("a plan that never runs out reports no shortfall", rich["failed_shortfall_today"], None)
    check("and reads above the band: possibly over-saving", rich["band"], "above")
    cheaper = retirement.retirement_plan(current_value=300_000, allocation=alloc, current_age=60, retirement_age=65, longevity_age=95,
                                         annual_income=60_000, discount_rate=0.06, success_ceiling=0.95)
    check("a higher rate makes the same spending worth less today", cheaper["pv_spending"] < poor["pv_spending"], True)

    print("\n=== The funded ratio ===")
    brain = {"totals": {"total_value": 1_000_000}, "planning": {"discount_rate": 0.0, "sources": {"discount_rate": "cash_return"}}}
    lk = {"goals": [{"name": "House", "kind": "property", "target_amount": 400_000, "years": 5},
                    {"name": "Retire", "kind": "retirement", "target_amount": 2_000_000, "years": 20}]}
    f = funded.ratio(brain, lk, {"pv_spending": 600_000})
    check("book over goals and retirement spending", f["ratio"], 1.0)
    check("a retirement goal is not counted twice", f["goals_counted"], ["House"])
    check("no tiers on file: every goal counts, and it says so", f["tiers"], "not_on_file")
    check("what is not counted is named", f["not_counted"], ["contributions", "future_earnings", "held_away"])
    f = funded.ratio(brain, lk, None)
    check("with no retirement plan, the retirement goal counts", sorted(f["goals_counted"]), ["House", "Retire"])
    tiered = {"goals": [{"name": "House", "kind": "property", "target_amount": 400_000, "years": 5, "tier": "needs"},
                        {"name": "Boat", "kind": "other", "target_amount": 300_000, "years": 5, "tier": "wishes"}]}
    f = funded.ratio(brain, tiered, None)
    check("with tiers on file, only needs count", (f["goals_counted"], f["tiers"]), (["House"], "on_file"))
    f = funded.ratio({"totals": {"total_value": 1_000_000}, "planning": {"discount_rate": 0.05, "sources": {"discount_rate": "firm"}}},
                     {"goals": [{"name": "House", "kind": "property", "target_amount": 1_000_000, "years": 10}]}, None)
    check("a goal is discounted over its years", f["pv_goals"], round(1_000_000 / 1.05 ** 10, 2))
    check("at the firm's rate, named", f["discount_whose"], "firm")
    check("no needs, no ratio", funded.ratio(brain, {"goals": []}, None)["ratio"], None)


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        hh = (await s.execute(select(Household).join(Goal, Goal.household_id == Household.id)
                              .where(Household.firm_id == firm.id, Household.left_on.is_(None)))).scalars().first()
        print(f"\n=== The financial plan: {hh.name} ===")
        plan = await financial_plan.for_household(s, hh.id, firm_id=firm.id)
        f, r = plan["funded"], plan["retirement"]
        check("it carries the funded ratio", f["ratio"] is not None and f["ratio"] > 0, True)
        check("on the cash return, named", (f["discount_rate"], f["discount_whose"]), (0.025, "cash_return"))
        check("the retirement plan carries its band", r["band"] in ("below", "within", "above"), True)
        check("and its ceiling", r["success_ceiling"], 0.95)
        check("and what the portfolio must pay, discounted", r["pv_spending"] is not None and r["pv_spending"] > 0, True)
        check("every goal carries its band", all(g.get("band") in ("below", "within", "above")
                                                 for g in (await _likely(s, firm, hh))["goals"]), True)

        research = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.RESEARCH_CIO))).scalars().first()
        if research is None:
            research = User(firm_id=firm.id, email="research@funded.test", full_name="Rina Research", role=UserRole.RESEARCH_CIO,
                            is_active=True, hashed_password="x")
            s.add(research)
            await s.flush()
        await assumptions.record(s, firm, research, changes=[
            {"figure": "discount_rate", "value": 0.06, "source": "Investment committee, 20 Nov 2026"},
            {"figure": "success_ceiling", "value": 0.9, "source": "Investment committee, 20 Nov 2026"}],
            reason="A rate for funded ratios", refresh=False)
        await s.commit()
        print("\n=== The firm's rate and ceiling ===")
        again = await financial_plan.for_household(s, hh.id, firm_id=firm.id)
        check("the firm's rate is used, named", (again["funded"]["discount_rate"], again["funded"]["discount_whose"]), (0.06, "firm"))
        check("a higher rate makes the needs worth less today", again["funded"]["pv_needs"] < f["pv_needs"], True)
        check("so the same book is better funded", again["funded"]["ratio"] > f["ratio"], True)
        check("the ceiling is the firm's", again["retirement"]["success_ceiling"], 0.9)
        out = await assumptions.read(s, firm)
        rate = next(x for x in out["figures"] if x["key"] == "discount_rate")
        check("the set names the rate as the firm's, with its source", (rate["whose"], rate["source"]),
              ("firm", "Investment committee, 20 Nov 2026"))

        print("\n=== The conversation ===")
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        said = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_financial_plan({"household_id": str(hh.id)})
        check("Astra's words name the funded ratio", "Funded ratio" in said["message"], True)
        check("and the firm's rate", "the firm's rate" in said["message"], True)


async def _likely(s, firm, hh):
    from app.aurea_core import firm_time
    from app.aurea_core.graph import household_brain
    return goal_tradeoff.likelihoods(await household_brain(s, hh.id, firm_id=firm.id), firm_time.today(firm))


async def main() -> int:
    engines()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
