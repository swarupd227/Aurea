"""How well funded a household's needs are, beside the likelihood they are met (B-177).

A likelihood alone hides how much. The funded ratio is the managed book over the present value of what it must pay:
each goal's target in the money of its date, and what the portfolio pays in retirement once guaranteed income is
taken off, both discounted to today at the assumption set's rate (the firm's, else the cash return in the same set,
named). The book is what is managed today: contributions and future earnings are not counted, and it says so.

Goals with a tier on file count only when they are needs. With no tier on file every goal counts, and it says so.
A retirement goal is not counted twice: the retirement plan's spending stands for it.

For an Indian household with a retirement stack on file (B-395, `retirement_stack.py`), the recorded value of its EPF,
PPF, NPS and SCSS accounts that the retirement plan counts is added as a named input, shown apart: the ratio includes it,
`ratio_without_stack` is the managed book alone against the needs as the plan without the stack reads them, and `assets`
stays the managed book. Contributions are still not counted here, and no other money held elsewhere is.

Beside the household's ratio, each goal has its own (B-394): the share of the managed book a person recorded for it,
over the present value of what that goal must pay. A goal with no share recorded is not assessed: nothing here shares
the book out between goals. A retirement goal's need is the retirement plan's spending, as in the household's ratio;
the same share over the goal's own target is given beside it, because the monthly saving is sized on that target.
"""
from __future__ import annotations

from app.aurea_core import planning

NEEDS = "needs"
# What no goal's ratio counts, named with it. Another goal's share is that goal's, never this one's.
NOT_COUNTED_BY_GOAL = ["contributions", "future_earnings", "held_away", "unassigned_book", "other_goals_shares"]


def by_goal(brain: dict, likelihoods: dict, retirement_plan: dict | None) -> list[dict]:
    """Each goal's own funded ratio, on the same rate and book as the household's. Shares that add to more than the
    whole are read as the goals page reads them (scaled to it)."""
    rate, _ = planning.discount_of(brain)
    book = float((brain.get("totals") or {}).get("total_value") or 0)
    stack = float(((retirement_plan or {}).get("stack") or {}).get("capital_counted") or 0)
    out = []
    for g in likelihoods.get("goals") or []:
        retirement = bool(retirement_plan and g.get("kind") == "retirement" and retirement_plan.get("pv_spending"))
        need = float(retirement_plan["pv_spending"]) if retirement \
            else float(g.get("target_amount") or 0) / (1 + rate) ** int(g.get("years") or 0)
        share = g.get("share")
        row = {"id": g.get("id"), "name": g.get("name"), "kind": g.get("kind"), "tier": g.get("tier"), "share": share,
               "need_basis": "retirement_spending" if retirement else "goal_target",
               "pv_need": round(need, 2) if need > 0 else None,
               "assets": round(book * float(share), 2) if share is not None else None}
        why = "no_funding_share" if share is None else "no_need" if need <= 0 else None
        own = stack if retirement else 0.0     # the stack is retirement money: it counts for that goal alone
        target = float(g.get("target_amount") or 0) / (1 + rate) ** int(g.get("years") or 0)
        out.append({**row, "status": "not_assessed" if why else "assessed", "why": why,
                    "ratio": None if why else round((book * float(share) + own) / need, 3),
                    "ratio_to_target": round(book * float(share) / target, 3) if retirement and share is not None and target > 0 else None,
                    **({"stack_assets": round(own, 2)} if own else {})})
    return out


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
    result = {
        "ratio": round(assets / needs, 3) if needs > 0 else None,
        "assets": round(assets, 2), "pv_needs": round(needs, 2), "pv_goals": round(pv_goals, 2),
        "pv_retirement": round(pv_retirement, 2), "discount_rate": rate,
        "discount_whose": "firm" if whose == "firm" else "cash_return",
        "goals_counted": [g["name"] for g in counted],
        "tiers": "on_file" if tiers_on_file else "not_on_file",
        "basis": "managed_book",
        "not_counted": ["contributions", "future_earnings", "held_away"],
        "by_goal": by_goal(brain, likelihoods, retirement_plan), "not_counted_by_goal": NOT_COUNTED_BY_GOAL,
        "shares_scaled": bool(likelihoods.get("shares_scaled")),
    }
    stack = (retirement_plan or {}).get("stack")
    if stack:
        held = float(stack.get("capital_counted") or 0)
        bare = pv_goals + float((stack.get("without") or {}).get("pv_spending") or 0)
        result.update({
            "ratio": round((assets + held) / needs, 3) if needs > 0 else None,
            "stack_assets": round(held, 2), "ratio_without_stack": round(assets / bare, 3) if bare > 0 else None,
            "pv_needs_without_stack": round(bare, 2), "basis": "managed_book_and_retirement_stack",
            "not_counted": ["contributions", "future_earnings", "other_held_away"],
            "not_counted_by_goal": ["contributions", "future_earnings", "other_held_away", "unassigned_book", "other_goals_shares"]})
    return result
