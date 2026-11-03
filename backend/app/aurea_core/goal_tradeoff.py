"""Multi-Goal Trade-Off Engine: the one answer to "will this goal be met?" (B-131).

The Canvas, the goals page, the staff planning view and the agents all read `likelihoods`, so no two
screens give one goal two figures. It works from what is on file: a goal's date (else its recorded
horizon), the share of the wealth recorded for it, its priority and the contributions recorded on it.
A goal with a share is funded from that share; the goals without one draw on the rest in priority
order, each residual compounding for the next. Whatever it has to assume is named in `assumed`.

Human capital, the present value of the career earnings still to come, is shown beside the goals from a
person's recorded income and date of birth. It is never paid into them: a salary is not savings.
Whether savings pay a retirement income is a different question, the retirement engine's
(aurea_core/retirement.py).
"""
from __future__ import annotations

from datetime import date

import numpy as np

from app.aurea_core import markets
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
    Multi-goal Monte Carlo over the household's capital.

    A goal with a recorded `share` of the wealth is funded from that share alone, with the contributions
    recorded on it; the rest of the wealth is one shared pool, which the goals with no share draw on in
    priority order (rank 1 first; within a rank the earlier goal first), each residual compounding for
    the next. What an earmarked goal leaves behind when it matures joins the shared pool. Contributions
    not recorded on a goal (`annual_contribution_total`, a what-if) are split as the wealth is.

    Career earnings (`human_capital_value`) are reported beside the goals and never paid into them: a
    salary is not savings, and only contributions on file are.
    """
    effective = round(total_portfolio + human_capital_value, 2)
    if not goals:
        return {"goals": [], "shared_capital": round(total_portfolio, 2), "human_capital": round(human_capital_value, 2),
                "total_effective_capital": effective, "assumptions": {}, "shares_scaled": False}

    mu, sigma = _blended_assumptions(allocation)
    rng = np.random.default_rng(seed)

    sorted_goals = sorted(goals, key=lambda g: (g.get("priority", 99), g.get("years", ASSUMED_YEARS)))
    max_years = max(1, min(max(int(g.get("years") or ASSUMED_YEARS) for g in sorted_goals), 50))
    shocks = rng.normal(mu, sigma, (sims, max_years))  # the same paths for every pool

    # Earmarked shares as recorded; shares that add to more than the whole are scaled to it, and say so.
    shares = {str(g["id"]): float(g["share"]) for g in sorted_goals if g.get("share") is not None}
    claimed = sum(shares.values())
    scaled = claimed > 1.0
    if scaled:
        shares = {k: v / claimed for k, v in shares.items()}
    rest = max(0.0, 1.0 - sum(shares.values()))

    own = {k: np.full(sims, float(total_portfolio) * v) for k, v in shares.items()}
    own_add = {str(g["id"]): float(g.get("contribution") or 0.0) + annual_contribution_total * shares[str(g["id"])]
               for g in sorted_goals if str(g["id"]) in shares}
    shared = np.full(sims, float(total_portfolio) * rest)
    shared_add = annual_contribution_total * rest + sum(float(g.get("contribution") or 0.0)
                                                        for g in sorted_goals if str(g["id"]) not in shares)

    due: dict[int, list[dict]] = {}
    for g in sorted_goals:
        due.setdefault(min(int(g.get("years") or ASSUMED_YEARS), max_years), []).append(g)

    goal_results: dict[str, dict] = {}
    for y in range(1, max_years + 1):
        growth = 1 + shocks[:, y - 1]
        shared = np.maximum(shared * growth + shared_add, 0.0)
        for k in own:
            own[k] = np.maximum(own[k] * growth + own_add[k], 0.0)
        for g in due.get(y, []):
            gid = str(g["id"])
            target = float(g.get("target_amount") or 0.0)
            avail = (own[gid] if gid in own else shared).copy()
            drawn = np.minimum(avail, target) if target > 0 else np.zeros(sims)
            if gid in own:
                shared = shared + np.maximum(own.pop(gid) - drawn, 0.0)   # what it leaves joins the shared pool
                own_add.pop(gid, None)
            else:
                shared = np.maximum(shared - drawn, 0.0)
            success = float(np.mean(avail >= target)) if target > 0 else 1.0
            med = float(np.median(avail))
            goal_results[gid] = {
                "id": gid,
                "name": g.get("name") or _kind_label(g.get("kind", "other")),
                "kind": g.get("kind", "other"),
                "priority": g.get("priority", 99),
                "share": round(shares[gid], 4) if gid in shares else None,
                "target_amount": round(target, 2),
                "years": y,
                "success_probability": round(success, 3),
                "on_track": success >= _TARGET_SUCCESS,
                "projected_median": round(med, 2),
                "projected_p10": round(float(np.percentile(avail, 10)), 2),
                "projected_p90": round(float(np.percentile(avail, 90)), 2),
                "shortfall": round(max(0.0, target - med), 2) if target > 0 else 0.0,
                "funded_amount": round(min(med, target) if target > 0 else med, 2),
            }

    return {
        "goals": [goal_results[str(g["id"])] for g in sorted_goals if str(g["id"]) in goal_results],
        "shared_capital": round(total_portfolio * rest, 2),
        "human_capital": round(human_capital_value, 2),
        "total_effective_capital": effective,
        "assumptions": {"expected_return": round(mu, 4), "volatility": round(sigma, 4), "sims": sims},
        "shares_scaled": scaled,
    }


# ── Trade-off matrix ──────────────────────────────────────────────────────────

def compute_tradeoff_matrix(
    *,
    goals: list[dict],
    total_portfolio: float,
    allocation: dict[str, float],
    annual_contribution_total: float = 0.0,
    human_capital_value: float = 0.0,
    ccy: str | None = None,
) -> list[dict]:
    """
    For each goal compute three what-if levers and show cross-goal impact.

    Levers per goal:
      - delay_2y: add 2 years to this goal's horizon
      - reduce_20pct: cut target amount by 20 %
      - contribute_5k: add 5,000 a year to household contributions, in the firm's currency
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
            ("contribute_5k", f"Add {markets.money(5000, ccy)} a year", None),
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


# ── What the engine works from ────────────────────────────────────────────────

ASSUMED_YEARS = 10           # a goal with neither a date nor a horizon on file
ASSUMED_RETIREMENT_AGE = 65  # career earnings stop here when no retirement age or date is on file


def age_of(dob: str | None, today: date) -> int | None:
    """Age on `today` from a date of birth as the brain carries it, or None with none on file."""
    if not dob:
        return None
    born = date.fromisoformat(str(dob)[:10])
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def horizon(goal: dict, today: date) -> tuple[int, str]:
    """Years to a goal and where they came from: its date, else the horizon recorded on it, else assumed."""
    a = goal.get("assumptions") or {}
    if goal.get("target_date"):
        days = (date.fromisoformat(str(goal["target_date"])[:10]) - today).days
        return max(1, round(days / 365.25)), "target_date"
    if a.get("years"):
        return max(1, int(a["years"])), "recorded"
    return ASSUMED_YEARS, "assumed"


def earnings(brain: dict, today: date, *, annual_income: float | None = None) -> dict:
    """Career earnings still to come, shown beside the goals: each living person's recorded income (where
    protection and tax read it), until the retirement age the retirement goal states or reaches on its
    date. An income is never worked out from wealth, so with none on file there is none. A what-if
    income replaces what is recorded, earned by the eldest person whose date of birth is on file."""
    ret = next((g for g in brain.get("goals", []) if g.get("kind") == "retirement"), None)
    stated = ((ret or {}).get("assumptions") or {}).get("retirement_age")
    living = [p for p in brain.get("persons", []) if not p.get("deceased_on")]
    if annual_income is not None:
        aged = sorted((p for p in living if p.get("date_of_birth")), key=lambda p: p["date_of_birth"])
        eldest = aged[0] if aged else {}
        streams, basis = [(eldest.get("full_name"), eldest, float(annual_income))], "what_if"
    else:
        streams = [(p.get("full_name"), p, float(v)) for p in living
                   if (v := ((p.get("profile") or {}).get("tax") or {}).get("annual_income")) is not None]
        basis = "recorded" if streams else "not_on_file"
    earners, assumed = [], set()
    for name, person, income in streams:
        age = age_of(person.get("date_of_birth"), today)
        if age is None:
            assumed.add("age")       # an income with no date of birth: its working years cannot be counted
            continue
        if stated:
            stops = int(stated)
        elif ret and (ret.get("target_date") or (ret.get("assumptions") or {}).get("years")):
            stops = age + horizon(ret, today)[0]
        else:
            stops = ASSUMED_RETIREMENT_AGE
            assumed.add("retirement_age")
        earners.append({"name": name, "age": age, "annual_income": income, "retirement_age": stops,
                        "value": human_capital_pv(age, stops, income)})
    return {"value": round(sum(e["value"] for e in earners), 2), "earners": earners, "basis": basis,
            "assumed": sorted(assumed)}


def prepare(brain: dict, today: date, *, priority_overrides: dict | None = None, goal_overrides: dict | None = None,
            annual_income: float | None = None) -> dict:
    """The goals as the engine runs them, the earnings beside them and the contributions recorded on them."""
    goals = []
    for g in brain.get("goals", []):
        gid, a = str(g.get("id") or g["name"]), g.get("assumptions") or {}
        years, basis = horizon(g, today)
        priority = (priority_overrides or {}).get(gid, g.get("priority") or 99)
        target = float(g.get("target_amount") or 0.0)
        ov = (goal_overrides or {}).get(gid) or {}
        years = max(1, years + int(ov.get("years_delta", 0)))
        target = target * float(ov.get("target_scale", 1.0))
        goals.append({"id": gid, "name": g.get("name") or _kind_label(g.get("kind", "other")),
                      "kind": g.get("kind", "other"), "priority": priority, "target_amount": round(target, 2),
                      "years": years, "horizon": basis, "assumptions": a,
                      "share": float(a["funding_share"]) if a.get("funding_share") is not None else None,
                      "contribution": float(a.get("annual_contribution") or 0.0)})
    extra = sum(float(((goal_overrides or {}).get(g["id"]) or {}).get("extra_contribution", 0)) for g in goals)
    earned = earnings(brain, today, annual_income=annual_income)
    what_if = bool(priority_overrides or goal_overrides or annual_income is not None)
    return {"goals": goals, "contributions": extra, "earnings": earned, "what_if": what_if,
            "assumed": sorted(set(earned["assumed"]) | ({"years"} if any(g["horizon"] == "assumed" for g in goals) else set()))}


def _annotate(results: list[dict], goals: list[dict]) -> list[dict]:
    by_id = {g["id"]: g for g in goals}
    return [{**r, "horizon": by_id[r["id"]]["horizon"],
             "assumed": ["years"] if by_id[r["id"]]["horizon"] == "assumed" else [], "estimate": True} for r in results]


def likelihoods(brain: dict, today: date, *, sims: int = 2500, **overrides) -> dict:
    """Each goal's likelihood from the brain alone. Every reader of a goal's likelihood comes here, so no
    two screens disagree about one goal."""
    inputs = prepare(brain, today, **overrides)
    total = brain["totals"]["total_value"] or 0.0
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}
    base = run_tradeoff(goals=inputs["goals"], total_portfolio=total, allocation=allocation,
                        annual_contribution_total=inputs["contributions"],
                        human_capital_value=inputs["earnings"]["value"], sims=sims)
    return {**base, "goals": _annotate(base["goals"], inputs["goals"]), "earnings": inputs["earnings"],
            "assumed": inputs["assumed"], "what_if": inputs["what_if"]}


# ── Household assembler ───────────────────────────────────────────────────────

async def for_household(
    session,
    household_id,
    *,
    firm_id,
    priority_overrides: dict[str, int] | None = None,   # goal_id -> new rank
    goal_overrides: dict[str, dict] | None = None,       # goal_id -> {years_delta, target_scale, extra_contribution}
    annual_income: float | None = None,                  # a what-if career income, saved nowhere
) -> dict | None:
    """The goals page: every goal's likelihood (`likelihoods`) and what each lever would do to the others."""
    from app.aurea_core import firm_time
    from app.aurea_core.graph import household_brain
    from app.models.tenant import Firm

    brain = await household_brain(session, household_id, firm_id=firm_id)
    if not brain:
        return None
    today = firm_time.today(await session.get(Firm, firm_id))
    total = brain["totals"]["total_value"] or 0.0
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}
    inputs = prepare(brain, today, priority_overrides=priority_overrides, goal_overrides=goal_overrides,
                     annual_income=annual_income)
    earned = inputs["earnings"]
    lead = earned["earners"][0] if earned["earners"] else None
    shared = {
        "household": {"id": str(household_id), "name": brain["household"]["name"]},
        "currency": brain.get("currency"), "invested": round(total, 2), "earnings": earned, "assumed": inputs["assumed"], "what_if": inputs["what_if"],
        # The figures the page lays out; None where nothing is on file, never a stand-in.
        "current_age": lead["age"] if lead else None,
        "retirement_age": lead["retirement_age"] if lead else None,
        "annual_income": round(sum(e["annual_income"] for e in earned["earners"]), 2) if lead else None,
    }
    if not inputs["goals"]:
        return {**shared, "goals": [], "shared_capital": round(total, 2), "human_capital": earned["value"],
                "total_effective_capital": round(total + earned["value"], 2), "tradeoff_matrix": [], "assumptions": {}}

    base = run_tradeoff(goals=inputs["goals"], total_portfolio=total, allocation=allocation,
                        annual_contribution_total=inputs["contributions"], human_capital_value=earned["value"])
    matrix = compute_tradeoff_matrix(goals=inputs["goals"], total_portfolio=total, allocation=allocation,
                                     annual_contribution_total=inputs["contributions"],
                                     human_capital_value=earned["value"], ccy=brain.get("currency"))
    return {
        **shared,
        "goals": _annotate(base["goals"], inputs["goals"]),
        "shared_capital": base["shared_capital"],
        "human_capital": base["human_capital"],
        "total_effective_capital": base["total_effective_capital"],
        "tradeoff_matrix": matrix,
        "assumptions": base["assumptions"],
    }


def _kind_label(kind: str) -> str:
    return {
        "retirement": "Retirement income",
        "education": "Education fund",
        "property": "Property purchase",
        "legacy": "Legacy / bequest",
    }.get(kind, kind.replace("_", " ").title())
