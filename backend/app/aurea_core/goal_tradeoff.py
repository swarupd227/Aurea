"""Multi-Goal Trade-Off Engine.

Runs simultaneous Monte Carlo across competing goals with one shared capital pool.
Goals are funded in priority order at their target year; remaining capital
continues to compound forward for lower-priority or later goals.

Human capital — the present value of remaining career earnings — is modelled
as an additional annual cash-flow that supplements portfolio contributions
rather than an investable lump sum (it arrives as salary, not a windfall).
"""
from __future__ import annotations

from datetime import date

import numpy as np

from app.aurea_core.planning import _blended_assumptions

_DISCOUNT_RATE = 0.04   # risk-free rate used to PV human capital
_TARGET_SUCCESS = 0.70  # "on track" threshold


# ── Human capital ─────────────────────────────────────────────────────────────

def human_capital_pv(
    current_age: int, retirement_age: int, annual_income: float,
    *, discount_rate: float = _DISCOUNT_RATE,
) -> float:
    """PV of remaining career earnings as an annuity."""
    years = max(0, retirement_age - current_age)
    if years == 0 or annual_income <= 0:
        return 0.0
    if discount_rate > 0:
        pv = annual_income * (1 - (1 + discount_rate) ** -years) / discount_rate
    else:
        pv = annual_income * years
    return round(pv, 2)


# ── Core MC ───────────────────────────────────────────────────────────────────

def run_tradeoff(
    *,
    goals: list[dict],
    total_portfolio: float,
    allocation: dict[str, float],
    annual_contribution_total: float = 0.0,
    human_capital_value: float = 0.0,
    sims: int = 2500,
    seed: int = 42,
) -> dict:
    """
    Multi-goal shared-capital Monte Carlo.

    Goals are processed in priority order (rank 1 = highest).  Within the same
    priority rank, earlier-maturing goals draw first.  After each draw the
    residual pool continues to compound for subsequent goals.

    Returns per-goal success probabilities and the shared-capital summary.
    """
    if not goals:
        return {
            "goals": [], "shared_capital": round(total_portfolio, 2),
            "human_capital": round(human_capital_value, 2),
            "total_effective_capital": round(total_portfolio + human_capital_value, 2),
            "assumptions": {},
        }

    mu, sigma = _blended_assumptions(allocation)
    rng = np.random.default_rng(seed)

    # Sort: priority ASC, then years ASC (ties broken by shorter horizon first)
    sorted_goals = sorted(goals, key=lambda g: (g.get("priority", 99), g.get("years", 10)))
    max_years = max(int(g.get("years") or 10) for g in sorted_goals)
    max_years = max(1, min(max_years, 50))

    # Pre-generate shocks so the paths are identical for each goal draw
    shocks = rng.normal(mu, sigma, (sims, max_years))  # (sims, years)

    # Human capital modelled as an annual cash-flow supplement
    hc_annual = human_capital_value / max_years if max_years else 0.0
    annual_add = annual_contribution_total + hc_annual

    # Portfolio balance per sim, evolved year by year
    balance = np.full(sims, float(total_portfolio))

    # Bucket goals by their maturity year (already sorted by priority within year)
    goals_by_year: dict[int, list[dict]] = {}
    for g in sorted_goals:
        yr = min(int(g.get("years") or 10), max_years)
        goals_by_year.setdefault(yr, []).append(g)

    # Simulate year-by-year, drawing capital when goals mature
    goal_results: dict[str, dict] = {}
    for y in range(1, max_years + 1):
        balance = np.maximum(balance * (1 + shocks[:, y - 1]) + annual_add, 0.0)

        if y not in goals_by_year:
            continue

        for g in goals_by_year[y]:
            target = float(g.get("target_amount") or 0.0)
            avail = balance.copy()

            if target > 0:
                success = float(np.mean(avail >= target))
                drawn = np.minimum(avail, target)
                shortfall = max(0.0, target - float(np.median(avail)))
            else:
                success = 1.0
                drawn = np.zeros(sims)
                shortfall = 0.0

            # Commit the draw — residual competes for subsequent goals
            balance = np.maximum(balance - drawn, 0.0)

            p10, med, p90 = float(np.percentile(avail, 10)), float(np.median(avail)), float(np.percentile(avail, 90))

            goal_results[str(g.get("id"))] = {
                "id": str(g.get("id")),
                "name": g.get("name") or _kind_label(g.get("kind", "other")),
                "kind": g.get("kind", "other"),
                "priority": g.get("priority", 99),
                "target_amount": round(target, 2),
                "years": y,
                "success_probability": round(success, 3),
                "on_track": success >= _TARGET_SUCCESS,
                "projected_median": round(med, 2),
                "projected_p10": round(p10, 2),
                "projected_p90": round(p90, 2),
                "shortfall": round(shortfall, 2),
                "funded_amount": round(min(float(np.median(avail)), target) if target > 0 else float(np.median(avail)), 2),
            }

    # Preserve original priority order in output
    ordered = [goal_results[str(g.get("id"))] for g in sorted_goals if str(g.get("id")) in goal_results]

    return {
        "goals": ordered,
        "shared_capital": round(total_portfolio, 2),
        "human_capital": round(human_capital_value, 2),
        "total_effective_capital": round(total_portfolio + human_capital_value, 2),
        "assumptions": {"expected_return": round(mu, 4), "volatility": round(sigma, 4), "sims": sims},
    }


# ── Trade-off matrix ──────────────────────────────────────────────────────────

def compute_tradeoff_matrix(
    *,
    goals: list[dict],
    total_portfolio: float,
    allocation: dict[str, float],
    annual_contribution_total: float = 0.0,
    human_capital_value: float = 0.0,
) -> list[dict]:
    """
    For each goal compute three what-if levers and show cross-goal impact.

    Levers per goal:
      - delay_2y: add 2 years to this goal's horizon
      - reduce_20pct: cut target amount by 20 %
      - contribute_5k: add $5 000/yr to household contributions
    """
    base = run_tradeoff(
        goals=goals, total_portfolio=total_portfolio, allocation=allocation,
        annual_contribution_total=annual_contribution_total,
        human_capital_value=human_capital_value, sims=1200,
    )
    base_by_id = {g["id"]: g["success_probability"] for g in base["goals"]}

    matrix = []
    for i, g in enumerate(goals):
        levers = []

        for lever_key, lever_label, mod_fn in [
            ("delay_2y", "Delay by 2 years", lambda g: dict(g, years=g.get("years", 10) + 2)),
            ("reduce_20pct", "Reduce target 20%", lambda g: dict(g, target_amount=(g.get("target_amount") or 0) * 0.80)),
            ("contribute_5k", "Add $5k/yr contribution", None),
        ]:
            modified = [dict(gg) for gg in goals]
            if lever_key == "contribute_5k":
                result = run_tradeoff(
                    goals=modified, total_portfolio=total_portfolio, allocation=allocation,
                    annual_contribution_total=annual_contribution_total + 5000,
                    human_capital_value=human_capital_value, sims=1200,
                )
            else:
                modified[i] = mod_fn(g)
                result = run_tradeoff(
                    goals=modified, total_portfolio=total_portfolio, allocation=allocation,
                    annual_contribution_total=annual_contribution_total,
                    human_capital_value=human_capital_value, sims=1200,
                )
            res_by_id = {r["id"]: r["success_probability"] for r in result["goals"]}
            levers.append({
                "key": lever_key,
                "label": lever_label,
                "impact": [
                    {
                        "id": r["id"], "name": r["name"],
                        "success_probability": res_by_id.get(r["id"], base_by_id.get(r["id"], 0)),
                        "delta": round(res_by_id.get(r["id"], 0) - base_by_id.get(r["id"], 0), 3),
                    }
                    for r in result["goals"]
                ],
            })

        matrix.append({"goal_id": str(g.get("id")), "goal_name": g.get("name"), "levers": levers})

    return matrix


# ── Insight text ──────────────────────────────────────────────────────────────

def _insight_text(goals: list[dict], human_capital_value: float) -> str:
    if not goals:
        return "No goals defined yet. Add goals to see how your capital stacks up."

    at_risk = [g for g in goals if not g["on_track"]]
    on_track = [g for g in goals if g["on_track"]]

    if not at_risk:
        hc_note = (
            f" Your career earnings (PV ${human_capital_value:,.0f}) provide a significant buffer."
            if human_capital_value > 50_000 else ""
        )
        return (
            f"All {len(goals)} goals are on track at current savings and priority settings.{hc_note} "
            f"Consider increasing targets or moving forward timelines to build additional margin."
        )

    funded_names = " and ".join(g["name"] for g in on_track[:2]) if on_track else None
    risk_names = " and ".join(g["name"] for g in at_risk[:2])

    if funded_names and at_risk:
        return (
            f"At your current savings rate, {funded_names} "
            f"{'is' if len(on_track) == 1 else 'are'} well-funded. "
            f"{risk_names} {'is' if len(at_risk) == 1 else 'are'} competing for the same capital. "
            f"Delaying or trimming one of the at-risk goals frees capital for the others — "
            f"use the levers below to see the trade-off."
        )

    total_shortfall = sum(g.get("shortfall", 0) for g in at_risk)
    return (
        f"{len(at_risk)} of {len(goals)} goals need attention. "
        f"Combined shortfall at median: ${total_shortfall:,.0f}. "
        f"Reprioritise goals or adjust timelines below to find a feasible plan."
    )


# ── Household assembler ───────────────────────────────────────────────────────

async def for_household(
    session,
    household_id,
    *,
    priority_overrides: dict[str, int] | None = None,   # goal_id -> new rank
    goal_overrides: dict[str, dict] | None = None,       # goal_id -> {years_delta, target_scale, extra_contribution}
    annual_income: float | None = None,
) -> dict | None:
    """Assemble multi-goal trade-off from the household brain."""
    from app.aurea_core.graph import household_brain
    from app.core.db import utcnow

    brain = await household_brain(session, household_id)
    if not brain:
        return None

    total = brain["totals"]["total_value"] or 0.0
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}

    # Derive current age from oldest adult
    this_year = utcnow().year
    birth_years = []
    for p in brain.get("persons", []):
        dob = p.get("date_of_birth")
        if dob:
            try:
                birth_years.append(int(str(dob)[:4]))
            except ValueError:
                pass
    current_age = (this_year - min(birth_years)) if birth_years else 54

    # Retirement goal for human-capital inputs
    raw_goals = brain.get("goals", [])
    ret_goal = next((g for g in raw_goals if g.get("kind") == "retirement"), None)
    ret_assumptions = (ret_goal or {}).get("assumptions") or {}
    retirement_age = int(ret_assumptions.get("retirement_age", max(65, current_age + 5)))

    # Annual income: explicit override > retirement-goal assumption > heuristic
    if annual_income is None:
        annual_income = float(ret_assumptions.get("annual_income") or total * 0.05)

    hc = human_capital_pv(current_age, retirement_age, annual_income)

    # Build goal list with derived years
    today = date.today()
    goals: list[dict] = []
    for g in raw_goals:
        a = g.get("assumptions") or {}
        gid = g["id"]

        # Derive years from target_date or assumptions.years
        if g.get("target_date"):
            try:
                td = date.fromisoformat(str(g["target_date"])[:10])
                years = max(1, (td - today).days // 365)
            except Exception:
                years = int(a.get("years", 10))
        else:
            years = int(a.get("years", 10))

        priority = priority_overrides.get(gid, g.get("priority") or 99) if priority_overrides else (g.get("priority") or 99)
        target = float(g.get("target_amount") or 0.0)

        # Apply per-goal overrides (what-if)
        if goal_overrides and gid in goal_overrides:
            ov = goal_overrides[gid]
            years = max(1, years + int(ov.get("years_delta", 0)))
            target = target * float(ov.get("target_scale", 1.0))

        goals.append({
            "id": gid,
            "name": g.get("name") or _kind_label(g.get("kind", "other")),
            "kind": g.get("kind", "other"),
            "priority": priority,
            "target_amount": round(target, 2),
            "years": years,
            "assumptions": a,
        })

    if not goals:
        return {
            "household": {"id": str(household_id), "name": brain["household"]["name"]},
            "goals": [], "shared_capital": round(total, 2), "human_capital": round(hc, 2),
            "total_effective_capital": round(total + hc, 2), "insight": "No goals defined.",
            "tradeoff_matrix": [], "assumptions": {},
            "current_age": current_age, "retirement_age": retirement_age, "annual_income": round(annual_income, 2),
        }

    # Extra contribution from goal overrides
    extra_contribution = sum(
        float((goal_overrides or {}).get(g["id"], {}).get("extra_contribution", 0)) for g in goals
    )
    total_annual_contribution = float(ret_assumptions.get("annual_contribution", 0.0)) + extra_contribution

    base = run_tradeoff(
        goals=goals, total_portfolio=total, allocation=allocation,
        annual_contribution_total=total_annual_contribution,
        human_capital_value=hc,
    )

    matrix = compute_tradeoff_matrix(
        goals=goals, total_portfolio=total, allocation=allocation,
        annual_contribution_total=total_annual_contribution,
        human_capital_value=hc,
    )

    insight = _insight_text(base["goals"], hc)

    return {
        "household": {"id": str(household_id), "name": brain["household"]["name"]},
        "goals": base["goals"],
        "shared_capital": base["shared_capital"],
        "human_capital": base["human_capital"],
        "total_effective_capital": base["total_effective_capital"],
        "insight": insight,
        "tradeoff_matrix": matrix,
        "assumptions": base["assumptions"],
        "current_age": current_age,
        "retirement_age": retirement_age,
        "annual_income": round(annual_income, 2),
    }


def _kind_label(kind: str) -> str:
    return {
        "retirement": "Retirement income",
        "education": "Education fund",
        "property": "Property purchase",
        "legacy": "Legacy / bequest",
    }.get(kind, kind.replace("_", " ").title())
