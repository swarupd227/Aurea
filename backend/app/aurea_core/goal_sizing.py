"""What each goal needs saved a month, level and stepped up, beside how funded it is and what kind of vehicle suits its
horizon (B-394, L200-2 India section 5).

The required monthly investment is arithmetic, not a likelihood (the one answer to "will it be met" stays
`goal_tradeoff.likelihoods`, shown beside it):

  target     the goal's target in the money of its date, as every screen reads it (`goal_tradeoff.prepare`).
  set aside  the share of the managed book recorded for the goal, grown to the date. With no share recorded nothing
             already held is counted for it, and it says so.
  return     the compound return the household's current mix is expected to earn on the firm's assumption set (the mean
             less half the variance), as `return_need` reads it: about an even chance, never a promise. No rate of this
             module's own, and with no managed holdings to take a mix from it is not assessed.
  level      the amount saved at the end of every month to the date that makes up the gap.
  step-up    the first year's monthly amount when it is raised once a year by the percentage the person states. There is
             no default percentage: with none stated there is no stepped figure and no comparison.

The comparison runs the same first amount both ways, level and stepped, year by year: what is saved on the goal today
where something is, else the stepped amount that reaches the target. It records nothing and changes nothing. A step-up
that is bought is a term of the client's signed SIP registration (`sip_step_up`), never this figure.
"""
from __future__ import annotations

import math
import uuid
from datetime import date

from app.aurea_core import firm_time, funded, goal_tradeoff, goal_vehicles, markets, planning, retirement
from app.aurea_core.graph import household_brain
from app.aurea_core.planning import _blended_assumptions
from app.models.tenant import Firm

STEP_MAX = 50.0
NOT_COUNTED = ["tax_on_returns", "costs_of_the_product_held", "held_away", "future_earnings", "unassigned_book"]
BASIS = ("instalments at the end of each month; a step-up raises the monthly amount once a year; the expected compound "
         "return of the current mix, so about an even chance rather than a likelihood")


class SizingError(ValueError):
    pass


def step_percent(v) -> float | None:
    """The yearly step-up a person states, as a percentage. None where they state none."""
    if v in (None, ""):
        return None
    try:
        pct = float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        raise SizingError("The step-up is a percentage a year, such as 10.") from None
    if not 0 < pct <= STEP_MAX:
        raise SizingError(f"The step-up is a percentage a year, more than 0 and at most {STEP_MAX:g}.")
    return round(pct, 2)


def _monthly_rate(annual: float) -> float:
    return (1 + annual) ** (1 / 12) - 1


def corpus(start: float, monthly: float, step: float, annual_return: float, years: int) -> list[float]:
    """The balance at the end of each year (year 0 is today): `start` growing, with `monthly` saved at the end of every
    month and raised by `step` (a fraction) at the start of each later year."""
    rm = _monthly_rate(annual_return)
    balance, out = float(start), [float(start)]
    for year in range(years):
        paid = monthly * (1 + step) ** year
        for _ in range(12):
            balance = balance * (1 + rm) + paid
        out.append(balance)
    return out


def required_monthly(target: float, set_aside: float, step: float, annual_return: float, years: int) -> float:
    """The first year's monthly saving that reaches `target` in `years`, rounded up to a whole unit of money. Nothing
    where what is set aside already grows to it."""
    gap = target - set_aside * (1 + annual_return) ** years
    if gap <= 0:
        return 0.0
    per_unit = corpus(0.0, 1.0, step, annual_return, years)[-1]
    return float(math.ceil(gap / per_unit - 1e-9))


def _paid(monthly: float, step: float, years: int) -> float:
    return sum(12 * monthly * (1 + step) ** y for y in range(years))


def _size(goal: dict, set_aside: float, rate: float, pct: float | None) -> dict:
    target, years = float(goal["target_amount"] or 0), int(goal["years"])
    if target <= 0:
        return {"status": "not_assessed", "why": "no_target"}
    flat = required_monthly(target, set_aside, 0.0, rate, years)
    out = {"status": "assessed", "level_monthly": flat, "level_total": round(_paid(flat, 0.0, years), 2),
           "set_aside_at_date": round(set_aside * (1 + rate) ** years, 2), "step_up": None}
    if pct is not None:
        first = required_monthly(target, set_aside, pct / 100, rate, years)
        out["step_up"] = {"percent": pct, "first_monthly": first, "last_monthly": round(first * (1 + pct / 100) ** (years - 1), 2),
                          "total": round(_paid(first, pct / 100, years), 2)}
    return out


def _compare(goal: dict, set_aside: float, rate: float, sizing: dict, on_file: float) -> dict | None:
    """The same first monthly amount saved level and stepped, year by year, beside the target. None without a stated
    step-up, or where nothing needs saving and nothing is saved."""
    step = sizing.get("step_up")
    if not step:
        return None
    start = on_file if on_file > 0 else step["first_monthly"]
    if start <= 0:
        return None
    years, pct = int(goal["years"]), step["percent"] / 100
    level, stepped = corpus(set_aside, start, 0.0, rate, years), corpus(set_aside, start, pct, rate, years)
    return {"first_monthly": round(start, 2), "first_monthly_basis": "saved_on_the_goal" if on_file > 0 else "reaches_the_target_stepped_up",
            "percent": step["percent"], "target": round(float(goal["target_amount"]), 2),
            "points": [{"year": y, "level": round(a), "stepped": round(b)} for y, (a, b) in enumerate(zip(level, stepped))],
            "level_end": round(level[-1]), "stepped_end": round(stepped[-1])}


def lay_out(brain: dict, today: date, retirement_plan: dict | None, *, market, goal_id: str | None = None,
            step_up_percent: float | None = None) -> dict:
    """Every goal (or one) sized, funded and mapped to vehicle categories, from the brain. Pure, and writes nothing."""
    pct = step_percent(step_up_percent)
    likely = goal_tradeoff.likelihoods(brain, today)
    ratios = funded.ratio(brain, likely, retirement_plan)
    prepared = {g["id"]: g for g in goal_tradeoff.prepare(brain, today)["goals"]}
    raw = {str(g.get("id") or g["name"]): g for g in brain.get("goals", [])}
    book = float((brain.get("totals") or {}).get("total_value") or 0)
    mix = {k: v for k, v in ((brain.get("totals") or {}).get("by_asset_class") or {}).items() if v}
    expected = None
    if mix:
        mean, vol = _blended_assumptions(mix, cma=planning.cma_of(brain))
        total = sum(mix.values())
        expected = {"mean": round(mean, 5), "volatility": round(vol, 5), "compound": round(mean - vol ** 2 / 2, 5),
                    "mix": {k: round(v / total, 4) for k, v in sorted(mix.items(), key=lambda kv: -kv[1])}}
    by_goal = {str(r["id"]): r for r in ratios["by_goal"]}
    rows = []
    for g in likely["goals"]:
        gid = str(g["id"])
        if goal_id and gid != str(goal_id):
            continue
        p, own = prepared[gid], raw.get(gid) or {}
        a = own.get("assumptions") or {}
        set_aside = book * float(g["share"]) if g.get("share") is not None else 0.0
        # What is saved on the goal today: this year's SIP instalments, else the contribution recorded on it.
        on_file = round(float(p["contribution"]) / 12, 2)
        if p["schedule"] is None and a.get("monthly_contribution") is not None:
            on_file = float(a["monthly_contribution"])
        sizing = {"status": "not_assessed", "why": "no_holdings"} if expected is None else _size(g, set_aside, expected["compound"], pct)
        short = round(max(sizing["level_monthly"] - on_file, 0), 2) if sizing["status"] == "assessed" and p["contribution_basis"] != "not_on_file" else None
        stepped_sips = [s for s in own.get("sips") or [] if s.get("step_up_percent")]
        rows.append({
            "goal_id": gid, "name": g["name"], "kind": g["kind"], "tier": g.get("tier"), "priority": g.get("priority"),
            "years": g["years"], "horizon": g["horizon"], "target_date": own.get("target_date"),
            "retirement_age": a.get("retirement_age"),
            "target": round(float(g["target_amount"]), 2), "target_basis": g["target_basis"], "target_today": g.get("target_today"),
            "index": g.get("index"), "inflation": g.get("inflation"), "assumed": g.get("assumed", []),
            "share": g.get("share"), "set_aside": round(set_aside, 2) if g.get("share") is not None else None,
            "saving_monthly": on_file if p["contribution_basis"] != "not_on_file" else None, "saving_basis": p["contribution_basis"],
            "registrations": [{"amount": s["amount"], "step_up_percent": s.get("step_up_percent"), "step_up_every_months": s.get("step_up_every_months"),
                               "step_up_cap": s.get("step_up_cap"), "ends_on": s.get("ends_on"), "simulated": s.get("simulated")} for s in own.get("sips") or []],
            "registered_step_up": bool(stepped_sips),
            "likelihood": g["success_probability"], "on_track": g["on_track"], "band": g.get("band"),
            "funded": by_goal.get(gid), "sizing": sizing, "short_by_monthly": short,
            "comparison": None if expected is None else _compare(g, set_aside, expected["compound"], sizing, on_file),
            "vehicles": goal_vehicles.for_goal(g["kind"], g["years"], today) if goal_vehicles.applies(market) else None,
        })
    sized = [r for r in rows if r["sizing"]["status"] == "assessed"]
    return {
        "currency": brain.get("currency"), "as_of": today.isoformat(), "estimate": True, "changes_nothing": True,
        "assumptions_source": planning.source_of(brain), "assumptions_version": planning.version_of(brain),
        "expected": expected, "step_up_percent": pct, "basis": BASIS, "not_counted": NOT_COUNTED,
        "managed_book": round(book, 2),
        "household_funded": {**{k: ratios[k] for k in ("ratio", "assets", "pv_needs", "discount_rate", "discount_whose", "tiers", "goals_counted", "not_counted")},
                             # India's retirement stack, a named input of the ratio, and the ratio without it (B-395).
                             **{k: ratios[k] for k in ("stack_assets", "ratio_without_stack") if k in ratios}},
        "not_counted_by_goal": ratios["not_counted_by_goal"], "shares_scaled": ratios["shares_scaled"],
        "total_level_monthly": round(sum(r["sizing"]["level_monthly"] for r in sized), 2) if sized else None,
        "total_step_up_monthly": round(sum(r["sizing"]["step_up"]["first_monthly"] for r in sized), 2) if sized and pct is not None else None,
        "goals": rows,
    }


async def for_household(session, household_id: uuid.UUID, *, firm_id: uuid.UUID, goal_id: str | None = None,
                        step_up_percent=None) -> dict | None:
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    firm = await session.get(Firm, firm_id)
    if goal_id and not any(str(g.get("id")) == str(goal_id) for g in brain.get("goals", [])):
        raise SizingError("That goal is not this household's.")
    plan = await retirement.for_household(session, household_id, firm_id=firm_id)
    out = lay_out(brain, firm_time.today(firm), plan, market=markets.of_household(firm, brain["household"].get("values")),
                  goal_id=goal_id, step_up_percent=step_up_percent)
    return {"household_id": str(household_id), "household": brain["household"]["name"], **out}
