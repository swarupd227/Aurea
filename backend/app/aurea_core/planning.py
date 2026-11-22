"""Planning, projection & risk engine (spec §6.5).

Goals-based projections with Monte-Carlo dispersion (including decumulation), whole-
portfolio risk analytics, and stress testing against market shocks. Pure-numpy so it runs
anywhere; a firm can swap in a preferred engine via a Conduit investment-engine connector."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Long-run capital-market assumptions by asset class (annual): (expected_return, volatility).
# Firms can override these via Firm.settings["cmas"] — see effective_cma().
CMA = {
    "equity": (0.075, 0.16),
    "fixed_income": (0.035, 0.06),
    "cash": (0.025, 0.01),
    "alternatives": (0.085, 0.13),
    "property": (0.06, 0.12),
    "commodity": (0.04, 0.18),
    "multi_asset": (0.06, 0.10),
}


def effective_cma(firm_settings: dict | None = None) -> dict:
    """Return CMA dict, merging firm-level overrides from settings["cmas"].

    Each key in settings["cmas"] can be a list/tuple of [return, volatility].
    Unknown or malformed entries are ignored so defaults always win as fallback.
    """
    out = dict(CMA)
    overrides = (firm_settings or {}).get("cmas") or {}
    for cls, val in overrides.items():
        if isinstance(val, (list, tuple)) and len(val) == 2:
            try:
                out[cls] = (float(val[0]), float(val[1]))
            except (TypeError, ValueError):
                pass
    return out


def cma_source(firm_settings: dict | None = None) -> str:
    """Whose capital market assumptions a projection runs on: "firm" when the firm has set any that
    `effective_cma` would use, else "platform" (the defaults above)."""
    for val in ((firm_settings or {}).get("cmas") or {}).values():
        if isinstance(val, (list, tuple)) and len(val) == 2:
            try:
                float(val[0]), float(val[1])
                return "firm"
            except (TypeError, ValueError):
                continue
    return "platform"


# A goal's own inflation, by index: the firm setting it is read from and the stated default used without one.
# A default used is named in a result's `assumed` as "<index>_inflation", never applied silently.
INFLATION_INDEXES = {"cpi": ("inflation", 0.025), "education": ("education_inflation", 0.05),
                     "healthcare": ("healthcare_inflation", 0.05)}


# When work stops and how long a plan runs, without one on file: the platform's, unless the market suggests its own.
PLATFORM_RETIREMENT_AGE = 65
PLATFORM_PLAN_TO_AGE = 95
# The success a projection must reach to read "on track", by engine, and the firm setting each is read from. Goals and
# retirement have had different bars (70% and 85%) since they were built; both now live in the assumption set, named,
# and a firm sets either with its source (B-174). Unchanged here, because moving one moves every client's figure.
SUCCESS_TARGETS = {"goals": ("goal_success_target", 0.70), "retirement": ("retirement_success_target", 0.85)}


def assumption_set(firm_settings: dict | None = None, jurisdiction: str | None = None) -> dict:
    """What a firm's projections run on: its returns (the firm's CMAs over the platform's) and each inflation
    index, with whether each came from the firm or is the stated default. The client brain carries it, so every
    engine that reads the brain reads the firm's figures.

    Between the firm and the platform sits the firm's market (`market_assumptions`, India row 5): a figure the firm
    has not set takes the market's suggestion where there is one, and says so ("market"), with its source in
    `market_basis`. A market with none keeps exactly the platform's defaults."""
    from app.aurea_core import market_assumptions

    s = firm_settings or {}
    inflation, assumed, sources = {}, [], {}
    for index, (key, default) in INFLATION_INDEXES.items():
        value, whose = market_assumptions.value(s, jurisdiction, key, float(default))
        if whose == "platform":
            assumed.append(f"{index}_inflation")
        inflation[index], sources[f"{index}_inflation"] = value, whose
    cma, source = effective_cma(s), cma_source(s)
    suggested = market_assumptions.for_market(jurisdiction)
    firm_classes = set((s.get("cmas") or {}).keys())
    for cls, key in market_assumptions.RETURN_KEYS.items():
        if key in suggested and cls in cma and cls not in firm_classes:
            cma[cls] = (float(suggested[key].value), cma[cls][1])
            source = "firm" if source == "firm" else "market"
    retirement_age, sources["retirement_age"] = market_assumptions.value(s, jurisdiction, "retirement_age", PLATFORM_RETIREMENT_AGE)
    plan_to_age, sources["plan_to_age"] = market_assumptions.value(s, jurisdiction, "plan_to_age", PLATFORM_PLAN_TO_AGE)
    targets = {}
    for engine, (key, default) in SUCCESS_TARGETS.items():
        targets[engine], sources[f"{engine}_success_target"] = market_assumptions.value(s, jurisdiction, key, float(default))
    version = s.get("assumption_version")
    return {"cma": {k: list(v) for k, v in cma.items()}, "source": source,
            "inflation": inflation, "inflation_assumed": assumed,
            "retirement_age": retirement_age, "plan_to_age": plan_to_age, "success_target": targets, "sources": sources,
            # The recorded version these figures are (B-174); None while the firm has recorded none.
            "version": int(version) if version not in (None, "") else None,
            "market_basis": [a.view() for a in suggested.values()]}


def target_of(brain: dict | None, engine: str) -> float:
    """The success an engine's projection must reach to read on track: the firm's, else the platform's (B-174)."""
    got = (((brain or {}).get("planning") or {}).get("success_target") or {}).get(engine)
    return float(got) if got is not None else float(SUCCESS_TARGETS[engine][1])


def version_of(brain: dict | None) -> int | None:
    """The recorded assumption-set version a brain's projections run on, or None while the firm has recorded none."""
    return ((brain or {}).get("planning") or {}).get("version")


def default_of(brain: dict | None, key: str, platform):
    """A planning default the brain carries (`retirement_age`, `plan_to_age`), else the platform's."""
    got = ((brain or {}).get("planning") or {}).get(key)
    return type(platform)(got) if got is not None else platform


def assumption_version(firm_settings: dict | None = None) -> str:
    """Which assumptions a firm's projections run on, so the model inventory can say which a review saw (B-189): the
    recorded version ("v3") once the firm has one (B-174), else a short fingerprint that changes whenever a return, a
    volatility or an inflation rate does."""
    import hashlib
    import json

    recorded = (firm_settings or {}).get("assumption_version")
    if recorded not in (None, ""):
        return f"v{int(recorded)}"

    blob = json.dumps(assumption_set(firm_settings), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def cma_of(brain: dict | None) -> dict | None:
    """The CMAs a client brain carries (`assumption_set`), or None for the platform's."""
    return ((brain or {}).get("planning") or {}).get("cma")


def source_of(brain: dict | None) -> str:
    return ((brain or {}).get("planning") or {}).get("source") or "platform"

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


def _blended_assumptions(allocation: dict[str, float], cma: dict | None = None) -> tuple[float, float]:
    """Expected return & volatility for an allocation (weights by asset class)."""
    _cma = cma or CMA
    total = sum(allocation.values()) or 1.0
    mu = sigma_var = 0.0
    weights = {}
    for cls, val in allocation.items():
        w = val / total
        weights[cls] = w
        mu += w * _cma.get(cls, _cma.get("multi_asset", CMA["multi_asset"]))[0]
    # Simple correlation-aware variance: assume 0.4 pairwise correlation across risk assets.
    classes = list(weights)
    cov = 0.0
    for i, ci in enumerate(classes):
        si = _cma.get(ci, _cma.get("multi_asset", CMA["multi_asset"]))[1]
        for j, cj in enumerate(classes):
            sj = _cma.get(cj, _cma.get("multi_asset", CMA["multi_asset"]))[1]
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
    cma: dict | None = None,
    success_target: float = SUCCESS_TARGETS["goals"][1],
) -> GoalProjection:
    """Monte-Carlo projection of a goal funded by a portfolio (handles decumulation). `success_target` is the success that
    reads on track: the firm's goal target (`target_of(brain, "goals")`)."""
    mu, sigma = _blended_assumptions(allocation, cma=cma)
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
        on_track=prob >= success_target,
        probability_of_success=round(prob, 3),
        projected_median=round(float(median), 2),
        projected_p10=round(float(p10), 2),
        projected_p90=round(float(p90), 2),
        target_amount=round(float(target_amount), 2),
        years=years,
        shortfall=round(float(shortfall), 2),
    )


def portfolio_risk(allocation: dict[str, float], total_value: float, cma: dict | None = None) -> dict:
    """Whole-portfolio risk analytics: expected return, volatility, parametric VaR."""
    mu, sigma = _blended_assumptions(allocation, cma=cma)
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
