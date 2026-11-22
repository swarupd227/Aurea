"""The firm's planning assumptions are one versioned record, and a new version asks for the reviews it causes (B-174).

    python -m tests.verify_assumption_set

Builds the demo firm from seed/run.py in a throwaway SQLite file (the model is never called), then:
  - with no version recorded, every figure is the platform's and says so, with the two success bars (70% for goals,
    85% for retirement) named in the set rather than buried in two engines;
  - only research and the portfolio team record a version; an adviser and the administrator are refused, though the
    administrator's capital market table writes a version through the same record;
  - a change needs a reason and, for each figure, a value inside its bounds and a source; a preview writes nothing;
  - recording adds version 1, writes it through to the firm's settings every engine reads, and puts it on the audit log;
  - the refresh measures each active household's goal likelihoods on the old set and the new, and gives each one that
    moved past the line a task for its recorded adviser, and changes nothing else;
  - every projection names the version, and the success targets, cash reserve and input age are read from the set;
  - a version is never edited: the next one is added beside it;
  - the conversation's Confirm card lays out each figure before and after with its source.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_assumptions_"), "assumptions.db")
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
from app.aurea_core import assumptions, firm_time, goal_tradeoff, plan_inputs, planning, platform_admin, retirement  # noqa: E402
from app.aurea_core.graph import household_brain  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS  # noqa: E402
from app.llm.executors import ExecutorError, ToolExecutors  # noqa: E402
from app.models.assumptions import AssumptionSetVersion  # noqa: E402
from app.models.engagement import Task  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Goal, Household  # noqa: E402
from app.models.identity import AuditEvent, User  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, exc=assumptions.AssumptionError) -> None:
    try:
        await coro
        check(label, "allowed", "refused")
    except exc as e:
        check(label, "refused", "refused")
        print(f"         ({e})")


def fig(out: dict, key: str) -> dict:
    return next(f for f in out["figures"] if f["key"] == key)


async def person(s, firm, role: UserRole, name: str) -> User:
    u = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == role, User.is_active.is_(True)))).scalars().first()
    if u is None:
        u = User(firm_id=firm.id, email=f"{role.value}@assumptions.test", full_name=name, role=role, is_active=True,
                 hashed_password="x")
        s.add(u)
        await s.flush()
    return u


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()

    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        research = await person(s, firm, UserRole.RESEARCH_CIO, "Rina Research")
        portfolio = await person(s, firm, UserRole.PORTFOLIO_TEAM, "Pat Portfolio")
        adviser = await person(s, firm, UserRole.ADVISER, "Ada Adviser")
        admin = await person(s, firm, UserRole.ADMIN, "Alex Admin")
        await s.commit()
        today = firm_time.today(firm)
        hh = (await s.execute(select(Household).join(Goal, Goal.household_id == Household.id)
                              .where(Household.firm_id == firm.id, Household.left_on.is_(None)))).scalars().first()

        print("\n=== Before any version ===")
        out = await assumptions.read(s, firm)
        check("no version is recorded", out["version"], None)
        check("every figure is the platform's", {f["whose"] for f in out["figures"]}, {"platform"})
        check("the goals bar is in the set", fig(out, "success_target.goals")["value"], 0.7)
        check("the retirement bar is in the set", fig(out, "success_target.retirement")["value"], 0.85)
        check("an input's age line is in the set", fig(out, "plan_input_days")["value"], 90)
        check("a platform figure says so", fig(out, "return.equity")["source"], "Platform default")
        brain = await household_brain(s, hh.id, firm_id=firm.id)
        lk = goal_tradeoff.likelihoods(brain, today)
        check("a projection names no version yet", lk["assumptions_version"], None)
        check("the model inventory keeps its fingerprint", planning.assumption_version(firm.settings).startswith("v"), False)
        before = {g["id"]: g["success_probability"] for g in lk["goals"]}

        print("\n=== Who may record one ===")
        change = [{"figure": "return.equity", "value": 0.02, "source": "House view, November 2026 outlook"}]
        await refused("an adviser may not", assumptions.prepare(s, firm, adviser, changes=change, reason="r"), assumptions.NotOwner)
        await refused("the administrator may not, outside the CMA table",
                      assumptions.prepare(s, firm, admin, changes=change, reason="r"), assumptions.NotOwner)
        check("the tool is research's and the portfolio team's", TOOLS["record_assumption_set"].roles_required,
              {UserRole.RESEARCH_CIO, UserRole.PORTFOLIO_TEAM})
        check("every staff role reads it", UserRole.ADVISER in TOOLS["read_assumption_set"].roles_required, True)

        print("\n=== What a change needs ===")
        await refused("a reason", assumptions.prepare(s, firm, research, changes=change, reason=" "))
        await refused("a known figure", assumptions.prepare(s, firm, research, changes=[{"figure": "return.crypto", "value": 0.1, "source": "x"}], reason="r"))
        await refused("a value in bounds", assumptions.prepare(s, firm, research, changes=[{"figure": "return.equity", "value": 7, "source": "x"}], reason="r"))
        await refused("a source for each figure", assumptions.prepare(s, firm, research, changes=[{"figure": "return.equity", "value": 0.02}], reason="r"))
        await refused("each figure once", assumptions.prepare(s, firm, research, changes=change + change, reason="r"))
        await refused("at least one figure", assumptions.prepare(s, firm, research, changes=[], reason="r"))
        plan = await assumptions.prepare(s, firm, research, changes=change, reason="Lower equity outlook")
        check("a preview names the next version", plan["version"], 1)
        check("a preview writes nothing", ((await s.execute(select(func.count(AssumptionSetVersion.id)))).scalar(),
                                           (firm.settings or {}).get("cmas")), (0, (firm.settings or {}).get("cmas")))
        check("a preview names the line as the platform's", plan["refresh_line_assumed"], True)

        print("\n=== The Confirm card ===")
        ex = ToolExecutors(s, research.id, research.role, firm.id)
        card = await ex.preview_change("record_assumption_set", {"changes": change, "reason": "Lower equity outlook"})
        rows = {r["label"]: r["value"] for r in (card or {}).get("rows", [])}
        check("it names the version", rows.get("Version"), "1")
        check("it lays out the figure before and after", rows.get("Expected return, equity"), "7.5% → 2%")
        check("and its source", rows.get("Source"), "House view, November 2026 outlook")
        check("and the line, as the platform's", rows.get("Refresh line"), "5% (platform default)")
        try:
            await ToolExecutors(s, research.id, research.role, firm.id).preview_change(
                "record_assumption_set", {"changes": [{"figure": "return.equity", "value": 0.02}], "reason": "r"})
            check("a change that would only fail is no card", "card", "refused")
        except ExecutorError:
            check("a change that would only fail is no card", "refused", "refused")

        print("\n=== Recording version 1 ===")
        tasks_before = (await s.execute(select(func.count(Task.id)).where(Task.firm_id == firm.id))).scalar()
        goals_before = [(g.id, g.target_amount, g.target_date) for g in (await s.execute(select(Goal))).scalars().all()]
        got = await assumptions.record(s, firm, research, changes=change, reason="Lower equity outlook")
        await s.commit()
        check("version 1 stands", got["version"], 1)
        check("the firm's settings carry it", (firm.settings["assumption_version"], firm.settings["cmas"]["equity"]), (1, [0.02, 0.16]))
        check("the figure is the firm's, with its source", (fig(got, "return.equity")["whose"], fig(got, "return.equity")["source"]),
              ("firm", "House view, November 2026 outlook"))
        check("its volatility is untouched and still the platform's", fig(got, "volatility.equity")["whose"], "platform")
        audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "assumptions.recorded"))).scalars().all()
        check("it is on the audit log with its reason", [(a.subject, a.detail.get("reason")) for a in audit],
              [("Assumptions v1", "Lower equity outlook")])
        r = got["recorded"]["refresh"]
        check("the refresh ran", r["run"], True)
        check("it measured households", r["measured"] > 0, True)
        check("equity at 2% moved some past the line", len(r["moved"]) > 0, True)
        check("every one moved past the line or changed on track",
              all(m["biggest_change"] > r["line"] or m["on_track_changed"] for m in r["moved"]), True)
        new_tasks = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == "assumptions"))).scalars().all()
        check("one task per household moved", (len(new_tasks), len({t.household_id for t in new_tasks})), (len(r["moved"]), len(r["moved"])))
        check("no other task was raised", (await s.execute(select(func.count(Task.id)).where(Task.firm_id == firm.id))).scalar(),
              tasks_before + len(r["moved"]))
        assigned = [t for t in new_tasks if t.assigned_to]
        check("each task with an adviser on record is theirs", len(assigned), sum(1 for m in r["moved"] if m["adviser"]))
        check("a task names the version", all(t.title.startswith("Assumptions v1 moved") for t in new_tasks), True)
        check("no goal was changed", [(g.id, g.target_amount, g.target_date) for g in (await s.execute(select(Goal))).scalars().all()],
              goals_before)

        print("\n=== Every projection names it ===")
        brain = await household_brain(s, hh.id, firm_id=firm.id)
        lk = goal_tradeoff.likelihoods(brain, today)
        check("the goals name version 1", lk["assumptions_version"], 1)
        after = {g["id"]: g["success_probability"] for g in lk["goals"]}
        check("and run on the lower return", all(after[k] <= before[k] for k in before), True)
        ret = await retirement.for_household(s, hh.id, firm_id=firm.id)
        check("the retirement plan names version 1", ret["assumptions_version"], 1)
        check("the model inventory names it", planning.assumption_version(firm.settings), "v1")

        print("\n=== The bars, the reserve and the input age come from the set ===")
        got = await assumptions.record(s, firm, portfolio, changes=[
            {"figure": "success_target.goals", "value": 0.95, "source": "Investment committee, 20 Nov 2026"},
            {"figure": "success_target.retirement", "value": 0.6, "source": "Investment committee, 20 Nov 2026"},
            {"figure": "plan_input_days", "value": 180, "source": "Planning policy 4.2"},
            {"figure": "reserve_months", "value": 12, "source": "Planning policy 4.3"},
            {"figure": "refresh_line", "value": 0.2, "source": "Planning policy 4.4"},
        ], reason="Committee review", refresh=False)
        await s.commit()
        check("the portfolio team may record one too", got["version"], 2)
        brain = await household_brain(s, hh.id, firm_id=firm.id)
        lk = goal_tradeoff.likelihoods(brain, today)
        check("the goals bar is the firm's", lk["success_target"], 0.95)
        check("on track reads against it", all(g["on_track"] == (g["success_probability"] >= 0.95) for g in lk["goals"]), True)
        ret = await retirement.for_household(s, hh.id, firm_id=firm.id)
        check("the retirement bar is the firm's", ret["target_success"], 0.6)
        check("an input's age line is the firm's", plan_inputs.line_of(firm), {"days": 180, "assumed": False})
        check("the cash reserve is the firm's", firm.settings["reserve_months"], 12)

        print("\n=== A version is never edited ===")
        rows = (await s.execute(select(AssumptionSetVersion).where(AssumptionSetVersion.firm_id == firm.id)
                                .order_by(AssumptionSetVersion.version))).scalars().all()
        check("two versions, side by side", [v.version for v in rows], [1, 2])
        check("version 1 still holds its own figures", (rows[0].figures["return.equity"], rows[0].figures["success_target.goals"]), (0.02, 0.7))
        check("version 1 still names its own change", rows[0].changed, ["return.equity"])
        check("version 2 kept version 1's source", rows[1].sources.get("return.equity"), "House view, November 2026 outlook")
        await refused("the same value again is no change", assumptions.prepare(
            s, firm, research, changes=[{"figure": "reserve_months", "value": 12, "source": "x"}], reason="r"))

        print("\n=== The administrator's capital market table writes a version ===")
        await platform_admin.set_cma(s, firm, admin, asset_class="fixed_income", expected_return=0.045, reason="Rates moved")
        await s.commit()
        out = await assumptions.read(s, firm)
        check("it is version 3", out["version"], 3)
        check("its figure is the firm's, named as the administrator's table",
              (fig(out, "return.fixed_income")["value"], fig(out, "return.fixed_income")["source"]),
              (0.045, "The administrator's capital market table"))
        check("the history lists all three", [v["version"] for v in out["history"]], [3, 2, 1])

        print("\n=== The conversation reads it ===")
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_assumption_set({})
        check("an adviser reads the set", (read["version"], len(read["figures"])), (3, len(assumptions.FIGURES)))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
