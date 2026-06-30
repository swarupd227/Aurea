"""Retirement income & decumulation engine (spec §6.5 — the 'will my money last?' question).

Pure-numpy, deterministic. Two-phase Monte-Carlo: accumulation (contributions until retirement)
then decumulation (inflation-adjusted income drawn until a longevity age). On top of the base
projection it solves for the sustainable income at a target success rate, stresses the plan with a
sequence-of-returns shock in the first year of retirement (the risk that dominates early decumulation),
and evaluates the levers an adviser actually pulls — retire later, trim spending, take more growth risk.

No I/O here; `for_household` (below) assembles the inputs from the client brain."""
from __future__ import annotations

import numpy as np

from app.aurea_core.planning import STRESS_SCENARIOS, _blended_assumptions


def _simulate(
    current_value: float, mu: float, sigma: float, *, acc_years: int, dec_years: int,
    income_at_retirement: float, inflation: float, annual_contribution: float,
    sims: int, seed: int, first_year_shock: float | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    paths = np.full(sims, float(current_value))
    for _ in range(acc_years):
        shocks = rng.normal(mu, sigma, sims)
        paths = np.maximum(paths * (1 + shocks) + annual_contribution, 0.0)
    at_retirement = paths.copy()

    depletion = np.full(sims, -1)  # year index of depletion in retirement; -1 = survived
    balance: list[tuple[int, float, float, float]] = []
    for y in range(dec_years):
        if first_year_shock is not None and y == 0:
            shocks = np.full(sims, first_year_shock)
        else:
            shocks = rng.normal(mu, sigma, sims)
        withdrawal = income_at_retirement * ((1 + inflation) ** y)
        paths = paths * (1 + shocks) - withdrawal
        newly = (paths <= 0) & (depletion < 0)
        depletion = np.where(newly, y, depletion)
        paths = np.maximum(paths, 0.0)
        p10, med, p90 = np.percentile(paths, [10, 50, 90])
        balance.append((y, float(p10), float(med), float(p90)))

    return {
        "success": float(np.mean(paths > 0)),
        "end": paths, "at_retirement": at_retirement, "depletion": depletion, "balance": balance,
    }


def _shift_to_growth(allocation: dict[str, float], frac: float = 0.10) -> dict[str, float]:
    """Move `frac` of the portfolio from defensive (cash/fixed income) into equity."""
    total = sum(allocation.values()) or 1.0
    out = dict(allocation)
    move = total * frac
    for defensive in ("cash", "fixed_income"):
        take = min(out.get(defensive, 0.0), move)
        out[defensive] = out.get(defensive, 0.0) - take
        out["equity"] = out.get("equity", 0.0) + take
        move -= take
        if move <= 0:
            break
    return out


def retirement_plan(
    *,
    current_value: float,
    allocation: dict[str, float],
    current_age: int,
    retirement_age: int,
    longevity_age: int,
    annual_income: float,
    annual_contribution: float = 0.0,
    inflation: float = 0.025,
    target_success: float = 0.85,
    sims: int = 2500,
    seed: int = 7,
) -> dict:
    """Full retirement projection + sustainable income + sequence risk + levers."""
    mu, sigma = _blended_assumptions(allocation)
    acc = max(0, int(retirement_age - current_age))
    dec = max(1, int(longevity_age - retirement_age))
    infl_to_ret = (1 + inflation) ** acc
    income_at_ret = annual_income * infl_to_ret

    base = _simulate(current_value, mu, sigma, acc_years=acc, dec_years=dec,
                     income_at_retirement=income_at_ret, inflation=inflation,
                     annual_contribution=annual_contribution, sims=sims, seed=seed)

    failed = base["depletion"][base["depletion"] >= 0]
    median_depletion_age = int(retirement_age + np.median(failed) + 1) if failed.size else None
    proj_at_ret = float(np.median(base["at_retirement"]))

    # Sustainable income (today's $) achieving target_success — binary search.
    def success_for_income(income_today: float, alloc_mu=mu, alloc_sigma=sigma) -> float:
        return _simulate(current_value, alloc_mu, alloc_sigma, acc_years=acc, dec_years=dec,
                         income_at_retirement=income_today * infl_to_ret, inflation=inflation,
                         annual_contribution=annual_contribution, sims=1200, seed=seed)["success"]

    lo, hi = 0.0, max(annual_income * 2.5, proj_at_ret * 0.09, 1.0)
    for _ in range(16):
        mid = (lo + hi) / 2
        if success_for_income(mid) >= target_success:
            lo = mid
        else:
            hi = mid
    sustainable = lo

    # Sequence-of-returns stress: a GFC-style crash in the first year of retirement.
    gfc = STRESS_SCENARIOS["gfc_2008"]
    tot = sum(allocation.values()) or 1.0
    crash_return = sum((v / tot) * gfc.get(c, 0.0) for c, v in allocation.items())
    seq = _simulate(current_value, mu, sigma, acc_years=acc, dec_years=dec,
                    income_at_retirement=income_at_ret, inflation=inflation,
                    annual_contribution=annual_contribution, sims=sims, seed=seed,
                    first_year_shock=crash_return)

    # Levers the adviser can pull.
    levers = []
    late = min(retirement_age + 3, longevity_age - 1)
    if late > retirement_age:
        s_late = _simulate(current_value, mu, sigma, acc_years=max(0, late - current_age),
                           dec_years=max(1, longevity_age - late),
                           income_at_retirement=annual_income * ((1 + inflation) ** max(0, late - current_age)),
                           inflation=inflation, annual_contribution=annual_contribution,
                           sims=1500, seed=seed)["success"]
        levers.append({"key": "retire_later", "label": f"Retire at {late} (+{late - retirement_age}y)",
                       "success": round(s_late, 3)})
    levers.append({"key": "trim_income", "label": "Reduce income 10%",
                   "success": round(success_for_income(annual_income * 0.9), 3)})
    growth = _shift_to_growth(allocation, 0.10)
    gmu, gsigma = _blended_assumptions(growth)
    levers.append({"key": "more_growth", "label": "Shift +10% to growth assets",
                   "success": round(success_for_income(annual_income, gmu, gsigma), 3)})

    balance_by_age = [{"age": retirement_age + y + 1, "p10": round(p10), "median": round(med),
                       "p90": round(p90)} for (y, p10, med, p90) in base["balance"]]

    return {
        "current_age": current_age, "retirement_age": retirement_age, "longevity_age": longevity_age,
        "years_to_retirement": acc, "years_in_retirement": dec,
        "income_target": round(annual_income), "income_at_retirement": round(income_at_ret),
        "current_value": round(current_value), "projected_at_retirement": round(proj_at_ret),
        "success_probability": round(base["success"], 3),
        "on_track": base["success"] >= target_success,
        "target_success": target_success,
        "median_depletion_age": median_depletion_age,
        "sustainable_income": round(sustainable),
        "income_gap": round(annual_income - sustainable),
        "sequence_risk": {
            "baseline": round(base["success"], 3),
            "early_crash": round(seq["success"], 3),
            "delta": round(base["success"] - seq["success"], 3),
            "crash_return": round(crash_return, 3),
        },
        "levers": levers,
        "balance_by_age": balance_by_age,
        "assumptions": {
            "expected_return": round(mu, 4), "volatility": round(sigma, 4),
            "inflation": inflation, "target_success": target_success,
        },
    }


async def for_household(session, household_id, *, overrides: dict | None = None) -> dict | None:
    """Assemble retirement inputs from the client brain (with sensible defaults) and project."""
    from app.aurea_core.graph import household_brain  # local import avoids a cycle

    brain = await household_brain(session, household_id)
    if not brain:
        return None
    overrides = overrides or {}

    total = brain["totals"]["total_value"] or 0.0
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}

    # Find the retirement goal (if any) to seed the inputs.
    goals = brain.get("goals", [])
    rgoal = next((g for g in goals if g.get("kind") == "retirement"), None)
    a = (rgoal or {}).get("assumptions") or {}

    # Primary person's age (oldest adult with a DOB), else default.
    from app.core.db import utcnow
    this_year = utcnow().year
    birth_years = []
    for p in brain.get("persons", []):
        dob = p.get("date_of_birth")
        if dob:
            try:
                birth_years.append(int(str(dob)[:4]))
            except ValueError:
                pass
    derived_age = (this_year - min(birth_years)) if birth_years else a.get("current_age", 54)
    current_age = int(overrides.get("current_age") or derived_age)

    # Retirement age: explicit, else current age + the goal's horizon, else a sensible default.
    default_ret_age = a.get("retirement_age")
    if default_ret_age is None:
        default_ret_age = current_age + int(a["years"]) if a.get("years") else max(65, current_age + 5)
    retirement_age = int(overrides.get("retirement_age") or default_ret_age)
    longevity_age = int(overrides.get("longevity_age") or a.get("longevity_age", 95))
    funding_share = float(a.get("funding_share", 0.7))
    current_value = float(overrides.get("current_value") or total * funding_share)

    # Income target (today's $): explicit, else a ~5% draw on the funded retirement pot. Anchoring to
    # the funded value (not the aspirational goal lump sum) keeps the projection realistic.
    default_income = a.get("annual_income") or current_value * 0.05
    annual_income = float(overrides.get("annual_income") or default_income)
    annual_contribution = float(overrides.get("annual_contribution") if overrides.get("annual_contribution") is not None
                                else a.get("annual_contribution", 0.0))

    plan = retirement_plan(
        current_value=current_value, allocation=allocation, current_age=current_age,
        retirement_age=max(retirement_age, current_age + 1), longevity_age=max(longevity_age, retirement_age + 1),
        annual_income=annual_income, annual_contribution=annual_contribution,
    )
    plan["household"] = {"id": str(household_id), "name": brain["household"]["name"]}
    plan["goal"] = rgoal["name"] if rgoal else "Retirement"
    plan["funding_share"] = funding_share
    plan["inputs_editable"] = ["retirement_age", "longevity_age", "annual_income", "annual_contribution"]
    return plan
