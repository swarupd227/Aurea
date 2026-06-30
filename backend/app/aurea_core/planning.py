"""Planning, projection & risk engine (spec §6.5).

Goals-based projections with Monte-Carlo dispersion (including decumulation), whole-
portfolio risk analytics, and stress testing against market shocks. Pure-numpy so it runs
anywhere; a firm can swap in a preferred engine via a Conduit investment-engine connector."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Long-run capital-market assumptions by asset class (annual). Deterministic, documented.
CMA = {
    "equity": (0.075, 0.16),
    "fixed_income": (0.035, 0.06),
    "cash": (0.025, 0.01),
    "alternatives": (0.085, 0.13),
    "property": (0.06, 0.12),
    "commodity": (0.04, 0.18),
    "multi_asset": (0.06, 0.10),
}

# Named stress scenarios: per-asset-class shock (instantaneous return).
STRESS_SCENARIOS = {
    "gfc_2008": {"equity": -0.40, "fixed_income": 0.05, "alternatives": -0.30,
                 "property": -0.35, "commodity": -0.20, "cash": 0.0, "multi_asset": -0.25},
    "covid_2020": {"equity": -0.34, "fixed_income": -0.02, "alternatives": -0.20,
                   "property": -0.25, "commodity": -0.30, "cash": 0.0, "multi_asset": -0.22},
    "rates_shock": {"equity": -0.12, "fixed_income": -0.15, "alternatives": -0.08,
                    "property": -0.20, "commodity": 0.05, "cash": 0.0, "multi_asset": -0.10},
    "inflation_spike": {"equity": -0.15, "fixed_income": -0.10, "alternatives": 0.02,
                        "property": -0.05, "commodity": 0.20, "cash": -0.02, "multi_asset": -0.08},
}


@dataclass
class GoalProjection:
    on_track: bool
    probability_of_success: float
    projected_median: float
    projected_p10: float
    projected_p90: float
    target_amount: float
    years: int
    shortfall: float


def _blended_assumptions(allocation: dict[str, float]) -> tuple[float, float]:
    """Expected return & volatility for an allocation (weights by asset class)."""
    total = sum(allocation.values()) or 1.0
    mu = sigma_var = 0.0
    weights = {}
    for cls, val in allocation.items():
        w = val / total
        weights[cls] = w
        mu += w * CMA.get(cls, CMA["multi_asset"])[0]
    # Simple correlation-aware variance: assume 0.4 pairwise correlation across risk assets.
    classes = list(weights)
    cov = 0.0
    for i, ci in enumerate(classes):
        si = CMA.get(ci, CMA["multi_asset"])[1]
        for j, cj in enumerate(classes):
            sj = CMA.get(cj, CMA["multi_asset"])[1]
            corr = 1.0 if i == j else 0.4
            cov += weights[ci] * weights[cj] * si * sj * corr
    return mu, float(np.sqrt(max(cov, 1e-9)))


def project_goal(
    *,
    current_value: float,
    allocation: dict[str, float],
    annual_contribution: float,
    annual_withdrawal: float,
    years: int,
    target_amount: float,
    sims: int = 2000,
    seed: int = 7,
) -> GoalProjection:
    """Monte-Carlo projection of a goal funded by a portfolio (handles decumulation)."""
    mu, sigma = _blended_assumptions(allocation)
    rng = np.random.default_rng(seed)
    years = max(1, int(years))
    paths = np.full(sims, float(current_value))
    for _ in range(years):
        shocks = rng.normal(mu, sigma, sims)
        paths = paths * (1 + shocks) + annual_contribution - annual_withdrawal
        paths = np.maximum(paths, 0.0)

    p10, median, p90 = np.percentile(paths, [10, 50, 90])
    prob = float(np.mean(paths >= target_amount)) if target_amount > 0 else 1.0
    shortfall = max(0.0, target_amount - median)
    return GoalProjection(
        on_track=prob >= 0.70,
        probability_of_success=round(prob, 3),
        projected_median=round(float(median), 2),
        projected_p10=round(float(p10), 2),
        projected_p90=round(float(p90), 2),
        target_amount=round(float(target_amount), 2),
        years=years,
        shortfall=round(float(shortfall), 2),
    )


def portfolio_risk(allocation: dict[str, float], total_value: float) -> dict:
    """Whole-portfolio risk analytics: expected return, volatility, parametric VaR."""
    mu, sigma = _blended_assumptions(allocation)
    # 1-year 95% parametric VaR.
    var95 = total_value * (1.645 * sigma - mu)
    return {
        "expected_return": round(mu, 4),
        "volatility": round(sigma, 4),
        "sharpe_proxy": round((mu - 0.025) / sigma, 3) if sigma else None,
        "var_95_1y": round(max(var95, 0.0), 2),
        "var_95_pct": round(max((1.645 * sigma - mu), 0.0), 4),
    }


def stress_test(allocation_values: dict[str, float], scenarios: list[str] | None = None) -> dict:
    """Apply named market shocks to current allocation values. Returns loss per scenario."""
    scenarios = scenarios or list(STRESS_SCENARIOS)
    total = sum(allocation_values.values()) or 1.0
    results = {}
    for name in scenarios:
        shock = STRESS_SCENARIOS.get(name, {})
        loss = 0.0
        for cls, val in allocation_values.items():
            loss += val * shock.get(cls, 0.0)
        results[name] = {
            "impact_value": round(loss, 2),
            "impact_pct": round(loss / total, 4),
            "post_stress_value": round(total + loss, 2),
        }
    return results
