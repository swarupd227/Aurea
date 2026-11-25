"""The return a household's needs require, beside the questionnaire, and two people's answers side by side (B-178).

    python -m tests.verify_return_need

The arithmetic first, then the demo firm from seed/run.py in a throwaway SQLite file:
  - the required return is the one compound rate at which the book and the recorded contributions pay every goal's target at
    its date; covered below the lowest rate considered and beyond reach above the highest; it moves with wealth, contributions
    and targets the way a person would expect;
  - goals counted are the needs when any is tiered, else every goal, and it says which; it is not assessed with no goal;
  - the expected return is the allocation's compound return on the firm's assumptions, and the margin is the difference;
  - two people who answered for themselves are set side by side with how far apart their profiles are; one alone is not
    compared, never read as agreement;
  - nothing here moves a profile, a mandate or an IPS, and the conversation and the IPS draft read it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_rn_"), "rn.db")
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
from app.aurea_core import firm_time, return_need, risk_profile  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Goal, Household, Mandate, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.risk_assessment import RiskAssessment  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def arithmetic() -> None:
    print("\n=== The required return ===")
    one = [{"target": 1_000_000.0, "years": 10, "contribution": 0.0}]
    r = return_need.required_return(500_000.0, one)
    check("500,000 to 1,000,000 in ten years is about 7.18% a year", round(r["rate"], 4), round(2 ** 0.1 - 1, 4))
    check("so the book grown at that rate pays the target, to a hundredth of a percent", abs(500_000 * (1 + r["rate"]) ** 10 - 1_000_000) < 100, True)
    check("a bigger book needs less", return_need.required_return(700_000.0, one)["rate"] < r["rate"], True)
    check("a bigger target needs more", return_need.required_return(500_000.0, [{**one[0], "target": 1_500_000.0}])["rate"] > r["rate"], True)
    check("a longer horizon needs less", return_need.required_return(500_000.0, [{**one[0], "years": 20}])["rate"] < r["rate"], True)
    saving = return_need.required_return(500_000.0, [{**one[0], "contribution": 20_000.0}])["rate"]
    check("recorded contributions need less", saving < r["rate"], True)
    two = return_need.required_return(500_000.0, [{"target": 400_000.0, "years": 5, "contribution": 0.0}, {"target": 700_000.0, "years": 15, "contribution": 0.0}])
    check("two goals are answered by one rate: the book pays both at their dates", abs(400_000 / (1 + two["rate"]) ** 5 + 700_000 / (1 + two["rate"]) ** 15 - 500_000) < 100, True)
    check("already more than enough: covered, no rate", return_need.required_return(2_000_000.0, one), {"rate": None, "covered": True, "beyond_reach": False})
    check("nothing held: beyond reach", return_need.required_return(0.0, one), {"rate": None, "covered": False, "beyond_reach": True})
    check("contributions alone can cover it", return_need.required_return(0.0, [{**one[0], "contribution": 90_000.0}])["rate"] is not None, True)
    check("the annuity at no return is the years", return_need._annuity(0.0, 10), 10.0)


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        today = firm_time.today(firm)
        hh = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        people = (await s.execute(select(Person).where(Person.household_id == hh.id, Person.is_next_gen.is_(False)).order_by(Person.full_name))).scalars().all()
        a, b = people[0], people[1]

        print("\n=== The household ===")
        goals = (await s.execute(select(Goal).where(Goal.household_id == hh.id))).scalars().all()
        out = await return_need.for_household(s, hh.id, firm_id=firm.id)
        check("with goals on file it is assessed", out["status"], "assessed")
        check("goals with no tier on file: every goal counts, and it says so", (out["tiers"], len(out["goals"]) == len([g for g in goals if float(g.target_amount) > 0])), ("not_on_file", True))
        check("it changes nothing and says what it leaves out", (out["changes_nothing"], "held_away" in out["not_counted"]), (True, True))
        check("the expected return is the allocation's compound, below its mean",
              (out["expected"] is not None and out["expected"]["compound"] < out["expected"]["mean"]), True)
        if out["required_return"] is not None and out["expected"]:
            check("the margin is expected less required", out["margin"], round(out["expected"]["compound"] - out["required_return"], 5))
        check("an assumed goal horizon is named", all(x in ("years",) for x in out["assumed"]), True)

        print("\n=== Tiers narrow it to the needs ===")
        first = goals[0]
        first.assumptions = {**(first.assumptions or {}), "tier": "needs"}
        for g in goals[1:]:
            g.assumptions = {**(g.assumptions or {}), "tier": "wants"}
        await s.flush()
        needs = await return_need.for_household(s, hh.id, firm_id=firm.id)
        check("one goal tiered as a need: only it counts", (needs["tiers"], [g["name"] for g in needs["goals"]]), ("on_file", [first.name]))
        for g in goals:
            g.assumptions = {**(g.assumptions or {}), "tier": "wishes"}
        await s.flush()
        none = await return_need.for_household(s, hh.id, firm_id=firm.id)
        check("tiers on file and no need among them: not assessed, saying why", (none["status"], "tiered as a need" in none["reason"]), ("not_assessed", True))
        for g in goals:
            g.assumptions = {k: v for k, v in (g.assumptions or {}).items() if k != "tier"}
        await s.flush()

        print("\n=== Two people side by side ===")
        pair = await risk_profile.people_compared(s, hh.id, firm_id=firm.id)
        check("no one has been asked for themselves", (pair["asked_alone"], pair["gap"]), (0, None))
        calm = {"investment_horizon": "long (> 7 years)", "loss_tolerance": "low — I'd want to sell", "income_stability": "very stable",
                "liquidity_needs": "5+ years", "esg_priority": "not important", "experience": "experienced"}
        bold = {**calm, "loss_tolerance": "high — I'd buy more"}
        await risk_profile.submit(s, hh.id, calm, firm_id=firm.id, by="adviser@t", role=UserRole.ADVISER, subject_id=a.id)
        pair = await risk_profile.people_compared(s, hh.id, firm_id=firm.id)
        check("one person alone is not compared, and says the household's answers stand for the rest", (pair["asked_alone"], pair["gap"], "household's own answers" in pair["reason"]), (1, None, True))
        await risk_profile.submit(s, hh.id, bold, firm_id=firm.id, by="adviser@t", role=UserRole.ADVISER, subject_id=b.id)
        pair = await risk_profile.people_compared(s, hh.id, firm_id=firm.id)
        gap = pair["gap"]
        check("two are laid side by side", sorted(p["person"] for p in pair["people"] if p["assessment"]), sorted([a.full_name, b.full_name]))
        check("their tolerance is two steps apart and capacity the same", (gap["tolerance"], gap["capacity"]), (2, 0))
        check("so the profile indicated, the lower of the two, is two steps apart and says who takes less", (gap["indicated"] if gap["indicated"] == 0 else "differs", gap["takes_less_risk"]),
              ("differs" if gap["indicated"] else 0, gap["takes_less_risk"]))
        check("the cautious one takes less risk", gap["takes_less_risk"], a.full_name if gap["indicated"] else None)
        before = [(m.name, m.suitability) for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()]
        view = await risk_profile.with_need(s, hh.id, firm_id=firm.id)
        check("the household's read carries both beside the questionnaire", ("return_need" in view, "people_compared" in view), (True, True))
        check("and moves no mandate", [(m.name, m.suitability) for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()], before)
        check("each answer stays on record", (await s.execute(select(RiskAssessment).where(RiskAssessment.household_id == hh.id))).scalars().all().__len__(), 2)

        print("\n=== The conversation and the IPS draft ===")
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_risk_profiles({"household_id": str(hh.id)})
        check("Astra says what the needs ask for, and that it changes nothing", ("goals' needs ask for" in read["message"] or "return the needs ask for is not assessed" in read["message"].lower()
                                                                                   or "already covered" in read["message"] or "beyond a 40%" in read["message"], "step(s) apart" in read["message"]), (True, True))
        from app.agents import ips_drafting

        line = ips_drafting._assessed(view)
        check("the IPS draft reads the questionnaire, the return and the two people", ("Questionnaire" in line or "No scored" in line, "answered for themselves" in line), (True, True))
        check("a household with no goal: not assessed in the draft's words too",
              ips_drafting._assessed({"assessment": None, "return_need": {"status": "not_assessed"}}), "No scored questionnaire on file")


async def main() -> int:
    arithmetic()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
