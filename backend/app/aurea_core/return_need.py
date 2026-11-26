"""The return a household's needs require, beside the questionnaire's tolerance and capacity (B-178, L200-2 §2.2).

Risk profiling has two legs, willingness (tolerance) and ability (capacity) to bear a loss, and a third that the
questionnaire cannot see: the return the goals the household must meet need from what it has. This works it out from what
is on file and sets it beside the result as a figure. It changes nothing: no profile, no mandate, no IPS term moves from it,
because a person decides what to do about a household whose needs ask for more than it is willing or able to take.

  required return   the one compound rate at which the managed book, plus the contributions recorded on those goals, pays
                    each goal's target in the money of its date. Pooled: the book is not shared out between goals, so one
                    rate answers all of them together. Goals counted are the needs when any goal has a tier on file, else
                    every goal, and it says which.
  expected return   the compound return the household's current allocation is expected to earn on the firm's capital market
                    assumptions (the mean less half the variance), named, never a promise.
  margin            expected less required. Positive means the allocation is expected to earn more than the goals need.

It is not assessed with no goal on file. A goal with no date reads on the horizon the goals page assumes, named. Money held
elsewhere, future earnings and contributions not recorded on a goal are not counted, and it says so.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.aurea_core import firm_time, goal_tradeoff, planning
from app.aurea_core.graph import household_brain
from app.aurea_core.planning import _blended_assumptions
from app.models.tenant import Firm

LOW, HIGH = -0.05, 0.40
NEEDS = "needs"
NOT_COUNTED = ["held_away", "future_earnings", "contributions_not_recorded_on_a_goal", "retirement_spending_beyond_the_goal_target"]
DECIDES = "nothing: it sits beside the questionnaire, and a person decides what to do about a gap"


def _annuity(rate: float, years: int) -> float:
    return float(years) if abs(rate) < 1e-9 else (1 - (1 + rate) ** -years) / rate


def _saved_today(rate: float, goal: dict) -> float:
    """What a goal's savings are worth today at `rate`: a level amount a year, or a SIP schedule's years (B-394)."""
    if goal.get("schedule") is None:
        return goal["contribution"] * _annuity(rate, goal["years"])
    return sum(v / (1 + rate) ** (k + 1) for k, v in enumerate(goal["schedule"][:goal["years"]]))


def _gap(rate: float, wealth: float, goals: list[dict]) -> float:
    """What the goals cost today, discounted at `rate`, less what the book and the contributions are worth today."""
    owed = sum(g["target"] / (1 + rate) ** g["years"] for g in goals)
    held = wealth + sum(_saved_today(rate, g) for g in goals)
    return owed - held


def required_return(wealth: float, goals: list[dict]) -> dict:
    """The compound rate that makes the book and the recorded contributions pay every goal at its date. Pure. Each goal is
    {target (in the money of its date), years, contribution (a year, until that date)}. `covered` means even the lowest
    rate considered (-5%) is enough; `beyond_reach` means the highest (40%) is not."""
    if _gap(LOW, wealth, goals) <= 0:
        return {"rate": None, "covered": True, "beyond_reach": False}
    if _gap(HIGH, wealth, goals) > 0:
        return {"rate": None, "covered": False, "beyond_reach": True}
    lo, hi = LOW, HIGH
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _gap(mid, wealth, goals) > 0 else (lo, mid)
    return {"rate": round((lo + hi) / 2, 5), "covered": False, "beyond_reach": False}


def expected_compound(brain: dict) -> dict | None:
    """What the current allocation is expected to compound at, from the assumption set the brain carries."""
    allocation = {k: v for k, v in ((brain.get("totals") or {}).get("by_asset_class") or {}).items() if v}
    if not allocation:
        return None
    mu, sigma = _blended_assumptions(allocation, cma=planning.cma_of(brain))
    return {"mean": round(mu, 5), "volatility": round(sigma, 5), "compound": round(mu - sigma ** 2 / 2, 5), "source": planning.source_of(brain)}


def lay_out(brain: dict, today: date) -> dict:
    prepared = goal_tradeoff.prepare(brain, today)
    goals = prepared["goals"]
    tiers_on_file = any(g.get("tier") for g in goals)
    counted = [g for g in goals if (g.get("tier") == NEEDS if tiers_on_file else True) and float(g.get("target_amount") or 0) > 0]
    base = {"decides": DECIDES, "not_counted": NOT_COUNTED, "changes_nothing": True, "estimate": True,
            "tiers": "on_file" if tiers_on_file else "not_on_file", "basis": "pooled: the managed book plus recorded contributions against the goals' targets, each in the money of its date"}
    if not counted:
        reason = ("No goal is tiered as a need, so there are no needs to work a return for." if tiers_on_file
                  else "No goal with a target is on file, so there is no need to work a return for.")
        return {**base, "status": "not_assessed", "reason": reason}
    wealth = float((brain.get("totals") or {}).get("total_value") or 0.0)
    rows = [{"target": float(g["target_amount"]), "years": int(g["years"]), "contribution": float(g.get("contribution") or 0.0),
             "schedule": g.get("schedule")} for g in counted]
    got = required_return(wealth, rows)
    expected = expected_compound(brain)
    required = got["rate"]
    assumed = [a for a in prepared["assumed"] if a == "years"]
    return {**base, "status": "assessed", "wealth": round(wealth, 2), "required_return": required, "already_covered": got["covered"], "beyond_reach": got["beyond_reach"],
            "range_considered": [LOW, HIGH], "expected": expected,
            "margin": round(expected["compound"] - required, 5) if expected and required is not None else None,
            "contributions_a_year": round(sum(r["contribution"] for r in rows), 2),
            "goals": [{"name": g["name"], "kind": g["kind"], "tier": g.get("tier"), "target": round(float(g["target_amount"]), 2), "years": int(g["years"]),
                       "horizon": g.get("horizon"), "contribution": float(g.get("contribution") or 0.0)} for g in counted],
            "assumed": assumed, "assumption_version": planning.version_of(brain)}


async def for_household(session, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    firm = await session.get(Firm, firm_id)
    return {"household_id": str(household_id), **lay_out(brain, firm_time.today(firm))}
