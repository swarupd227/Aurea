"""How well funded a household's needs are, beside the likelihood they are met (B-177).

A likelihood alone hides how much. The funded ratio is the managed book over the present value of what it must pay:
each goal's target in the money of its date, and what the portfolio pays in retirement once guaranteed income is
taken off, both discounted to today at the assumption set's rate (the firm's, else the cash return in the same set,
named). The book is what is managed today: contributions and future earnings are not counted, and it says so.

Goals with a tier on file count only when they are needs. With no tier on file every goal counts, and it says so.
A retirement goal is not counted twice: the retirement plan's spending stands for it.
"""
from __future__ import annotations

from app.aurea_core import planning

NEEDS = "needs"


def ratio(brain: dict, likelihoods: dict, retirement_plan: dict | None) -> dict:
    rate, whose = planning.discount_of(brain)
    goals = likelihoods.get("goals") or []
    tiers_on_file = any(g.get("tier") for g in goals)
    counted = [g for g in goals if (g.get("tier") == NEEDS if tiers_on_file else True)
               and not (retirement_plan and g.get("kind") == "retirement")]
    pv_goals = sum(float(g["target_amount"] or 0) / (1 + rate) ** int(g.get("years") or 0) for g in counted)
    pv_retirement = float((retirement_plan or {}).get("pv_spending") or 0)
    needs = pv_goals + pv_retirement
    assets = float((brain.get("totals") or {}).get("total_value") or 0)
    return {
        "ratio": round(assets / needs, 3) if needs > 0 else None,
        "assets": round(assets, 2), "pv_needs": round(needs, 2), "pv_goals": round(pv_goals, 2),
        "pv_retirement": round(pv_retirement, 2), "discount_rate": rate,
        "discount_whose": "firm" if whose == "firm" else "cash_return",
        "goals_counted": [g["name"] for g in counted],
        "tiers": "on_file" if tiers_on_file else "not_on_file",
        "basis": "managed_book",
        "not_counted": ["contributions", "future_earnings", "held_away"],
    }
