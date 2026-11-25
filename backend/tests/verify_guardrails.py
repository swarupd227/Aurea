"""Guardrail spending, run beside fixed spending, with the client's terms recorded on the IPS (B-180).

    python -m tests.verify_guardrails

The rule's arithmetic on small deterministic cases first, then the demo firm from seed/run.py in a throwaway SQLite file:
  - the first draw is the start rate of the pot; the draw then rises with inflation, not after a down year if the terms say so;
    it is cut past the upper guardrail and raised past the lower one, by the terms' percentages;
  - it runs on the same random returns as the plan, beside fixed spending: starting draw, likelihood, how often and how deep;
  - terms are the client's: the four of upper, lower, cut and raise are required, none is filled in, a start rate left out is
    the plan's own and says so; no terms given and none on the IPS is not assessed;
  - the terms are recorded on the IPS by an adviser, as a new version that carries every other term over, from a document and
    with a reason, and are carried to the next version; nothing changes spending, a plan or a mandate.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_gr_"), "gr.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

import numpy as np  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import firm_time, guardrails, ips, retirement  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ExecutorError, ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Household, Mandate, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.portfolio import InvestmentPolicy  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []
TERMS = {"start_rate": 0.05, "upper_band": 0.20, "lower_band": 0.20, "cut": 0.10, "raise": 0.10, "skip_inflation": False}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, guardrails.GuardrailError, ips.IpsError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def run(terms: dict, *, mu=0.0, sigma=0.0, pot=1_000_000.0, years=6, inflation=0.0) -> dict:
    return retirement.simulate_guardrails(pot, mu, sigma, acc_years=0, dec_years=years, annual_contribution=0.0, sims=4, seed=1, inflation=inflation,
                                          terms=terms, start_rate=terms["start_rate"])


def arithmetic() -> None:
    print("\n=== The rule, on deterministic paths (no volatility) ===")
    flat = run(TERMS, mu=0.0)
    check("the first draw is the start rate of the pot", float(flat["first_draw"][0]), 50_000.0)
    check("at no return and no inflation the rate drifts up: 50,000 on a falling pot is cut once it passes 6%", int(flat["cuts"][0]) >= 1, True)
    rich = run(TERMS, mu=0.10)
    check("a pot growing faster than it is drawn: the rate falls below the lower guardrail and the draw is raised", int(rich["raises"][0]) >= 1, True)
    check("and is never cut", int(rich["cuts"][0]), 0)
    calm = run({**TERMS, "upper_band": 0.60, "lower_band": 0.60}, mu=0.05, inflation=0.0, years=4)
    check("wide guardrails, a return near the draw: no cut and no raise", (int(calm["cuts"][0]), int(calm["raises"][0])), (0, 0))
    print("\n=== Inflation, and skipping it after a down year ===")
    wide = {**TERMS, "upper_band": 0.60, "lower_band": 0.60}
    check("the draw is raised with inflation after the first year: 50,000 then 55,000 leaves 895,000", float(run(wide, mu=0.0, inflation=0.10, years=2)["end"][0]), 895_000.0)
    down = {**wide, "skip_inflation": False}
    skip = {**wide, "skip_inflation": True}
    check("after a down year inflation is applied when the terms do not skip it: 850,000 less 55,000 over a further -10%", round(float(run(down, mu=-0.10, inflation=0.10, years=2)["end"][0])), 710_000)
    check("and not when they do: 850,000 less 50,000 over a further -10%", round(float(run(skip, mu=-0.10, inflation=0.10, years=2)["end"][0])), 715_000)
    check("the same seed gives the same returns as the plan's own simulation",
          float(retirement._simulate(1_000_000.0, 0.05, 0.12, acc_years=3, draws=[0.0] * 3, annual_contribution=0.0, sims=4, seed=5)["at_retirement"][0]),
          float(retirement.simulate_guardrails(1_000_000.0, 0.05, 0.12, acc_years=3, dec_years=1, annual_contribution=0.0, sims=4, seed=5, inflation=0.0,
                                               terms=TERMS, start_rate=0.05)["at_retirement"][0]))
    base = {**TERMS, "upper_band": 0.60, "lower_band": 0.60}
    applied = retirement.simulate_guardrails(1_000_000.0, 0.0, 0.0, acc_years=0, dec_years=3, annual_contribution=0.0, sims=2, seed=1, inflation=0.10,
                                             terms=base, start_rate=0.05)
    check("with inflation 10% a flat-return path draws 50,000 then 55,000 before any guardrail", float(applied["first_draw"][0]), 50_000.0)
    check("depth: the lowest real income is a share of the first year's, at most 100%", bool(np.all(flat["lowest"] <= 1.0 + 1e-9)), True)

    print("\n=== Terms are the client's ===")
    check("terms read as fractions, skip defaulting to no", guardrails.clean({"upper_band": 0.2, "lower_band": 0.2, "cut": 0.1, "raise": 0.1})["skip_inflation"], False)
    for missing in ("upper_band", "lower_band", "cut", "raise"):
        t = {k: v for k, v in TERMS.items() if k != missing}
        try:
            guardrails.clean(t)
            check(f"{missing} left out is refused", False, True)
        except guardrails.GuardrailError as e:
            check(f"{missing} left out is refused, naming it", guardrails.LABELS[missing].lower() in str(e).lower(), True)
    try:
        guardrails.clean({**TERMS, "cut": 0.9})
        check("a cut of 90% is refused", False, True)
    except guardrails.GuardrailError as e:
        check("a cut of 90% is refused", "between" in str(e), True)
    check("a start rate may be left out", guardrails.clean({**TERMS, "start_rate": None})["start_rate"], None)


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        para = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.PARAPLANNER))).scalars().first()
        hh = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        today = firm_time.today(firm)
        ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)

        print("\n=== No terms given and none on the IPS ===")
        out = await guardrails.for_household(s, hh.id, firm_id=firm.id)
        check("not assessed, and says what to give", (out["status"], "Say the terms" in out["reason"]), ("not_assessed", True))
        read = await ex.run_guardrail_spending({"household_id": str(hh.id)})
        check("Astra says it is not assessed", "not assessed" in read["message"], True)

        print("\n=== Terms said in the conversation ===")
        said = {"household_id": str(hh.id), "start_rate_pct": 5, "upper_pct": 20, "lower_pct": 20, "cut_pct": 10, "raise_pct": 10}
        out = await ex.run_guardrail_spending(said)
        check("it is assessed, from the terms said", (out["status"], out["terms_from"]), ("assessed", "stated"))
        check("the terms are as fractions", (out["terms"]["start_rate"], out["terms"]["cut"], out["terms"]["upper_band"]), (0.05, 0.10, 0.20))
        check("it sets the first draw and the likelihood beside the fixed plan's", all(k in out for k in ("start_draw_today", "fixed_draw_today", "success", "fixed_success")), True)
        check("how often and how deep", all(0 <= out[k] <= 1 for k in ("paths_with_a_cut", "share_of_years_cut", "lowest_income_median", "lowest_income_worst_tenth")), True)
        check("the worst tenth is no better than the median", out["lowest_income_worst_tenth"] <= out["lowest_income_median"], True)
        check("and says nothing changes spending by itself", "nothing changes spending by itself" in out["message"], True)
        plan = await retirement.for_household(s, hh.id, firm_id=firm.id)
        check("the plan itself carries no guardrails block", "guardrails" in plan, False)
        check("the fixed side is the plan's own likelihood", out["fixed_success"], plan["success_probability"])
        derived = await ex.run_guardrail_spending({k: v for k, v in said.items() if k != "start_rate_pct"})
        check("a start rate left out is the plan's own, and says so", (derived["terms"]["start_rate_derived"], "plan's own" in derived["message"]), (True, True))
        await refused("three of the four terms", ex.run_guardrail_spending({"household_id": str(hh.id), "upper_pct": 20, "lower_pct": 20, "cut_pct": 10}), "raise")
        await refused("a number that is not", ex.run_guardrail_spending({**said, "cut_pct": "lots"}), "must be a number")
        await refused("another firm's household", ex.run_guardrail_spending({"household_id": "00000000-0000-0000-0000-000000000000", **{k: v for k, v in said.items() if k != "household_id"}}), "not found")

        print("\n=== Recording the terms on the IPS ===")
        owners = [p.id for p in (await s.execute(select(Person).where(Person.household_id == hh.id))).scalars().all()]
        mandate = (await s.execute(select(Mandate).where(Mandate.person_id.in_(owners), Mandate.is_active.is_(True)).order_by(Mandate.name))).scalars().first()
        record = {"mandate_id": str(mandate.id), "accepted_on": today.isoformat(), "document": "Retirement income agreement, signed 2026", "reason": "The client agreed a spending rule",
                  "upper_pct": 20, "lower_pct": 20, "cut_pct": 10, "raise_pct": 10, "start_rate_pct": 5}
        await refused("no IPS in force to carry them", ex.record_guardrail_terms(record), "no IPS in force")
        base = await ips.record(s, mandate.id, firm_id=firm.id, accepted_on=today, document="IPS signed 2026", by="a@t", role="adviser")
        was = await ips.in_force(s, mandate.id)
        check("an IPS is in force, with no guardrail terms", (was.version, was.guardrail_spending), (base["version"], None))
        card = await ex.preview_change("record_guardrail_terms", record)
        rows = {r["label"]: r["value"] for r in card["rows"]}
        check("the card names the terms and that every other is carried over", (rows["Start rate"], rows["Cut"], rows["Every other term"]), ("5.00%", "10%", "Carried over unchanged"))
        r = await ex.record_guardrail_terms(record)
        now = await ips.in_force(s, mandate.id)
        check("a new version of the IPS holds them", (now.version, now.guardrail_spending["cut"], now.guardrail_spending["start_rate"]), (was.version + 1, 0.10, 0.05))
        check("every other term is carried over unchanged", (dict(now.targets), float(now.drift_band), list(now.restricted)), (dict(was.targets), float(was.drift_band), list(was.restricted)))
        check("it says it changes no spending", "change no spending" in r["message"], True)
        await refused("the same terms again", ex.record_guardrail_terms(record), "terms of version")
        await refused("a plain adviser-only act: a paraplanner cannot",
                      ToolExecutors(s, para.id, para.role, firm.id).record_guardrail_terms(record), "adviser")
        await refused("no reason", ex.record_guardrail_terms({**record, "reason": " ", "cut_pct": 12}), "reason")
        await refused("a term left out", ex.record_guardrail_terms({k: v for k, v in record.items() if k != "raise_pct"} | {"reason": "again"}), "raise")

        print("\n=== The IPS terms are read, and carried to the next version ===")
        out = await ex.run_guardrail_spending({"household_id": str(hh.id)})
        check("with none said, the IPS's terms are read", (out["status"], out["terms_from"], out["ips"]["ips_version"]), ("assessed", "ips", now.version))
        check("and name the IPS and the document the terms came from", (out["ips"]["mandate"], out["ips"]["document"]), (mandate.name, "Retirement income agreement, signed 2026"))
        await ips.record(s, mandate.id, firm_id=firm.id, accepted_on=today, document="IPS signed 2026, amended", by="a@t", role="adviser", reason="Band widened",
                         drift_band=0.07, targets={k: float(v) for k, v in now.targets.items()})
        nxt = await ips.in_force(s, mandate.id)
        check("a later version with no new guardrail terms carries them", (nxt.version, nxt.guardrail_spending), (now.version + 1, now.guardrail_spending))
        await ex.record_guardrail_terms({**record, "reason": "Cut widened at the review", "cut_pct": 15})
        newest = await ips.in_force(s, mandate.id)
        check("new terms replace the last, and the old version keeps its own", (newest.guardrail_spending["cut"], (await s.get(InvestmentPolicy, now.id)).guardrail_spending["cut"]), (0.15, 0.10))
        check("versions are added, never edited", (await s.execute(select(func.count(InvestmentPolicy.id)).where(InvestmentPolicy.mandate_id == mandate.id))).scalar(), newest.version)

        print("\n=== Who may ===")
        check("running is a read; recording is a Confirm card", (TOOLS["run_guardrail_spending"].change_state, TOOLS["record_guardrail_terms"].change_state),
              (ToolChangeState.NO, ToolChangeState.PAUSE_FOR_CONFIRMATION))
        check("no client holds either", any(UserRole.CLIENT in TOOLS[k].roles_required for k in ("run_guardrail_spending", "record_guardrail_terms")), False)
        check("recording is an adviser's alone", TOOLS["record_guardrail_terms"].roles_required, {UserRole.ADVISER})


async def main() -> int:
    arithmetic()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
