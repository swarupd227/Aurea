"""Shared signal-scanning over a household 'brain' snapshot.

Centralises the analytics reused by Meeting Prep, Research & Reporting, Next-Best-Action and
Client Care so each agent draws on one consistent view (spec 'agents are consumers of the
client brain, never owners of their own siloed copy')."""
from __future__ import annotations

from app.aurea_core import planning


def positions_of(brain: dict) -> list[dict]:
    return [p for acc in brain.get("accounts", []) for p in acc.get("positions", [])]


def book_signals(brain: dict) -> list[dict]:
    """Opportunities / risks / anomalies for a single household, each with a priority."""
    out: list[dict] = []
    total = brain["totals"]["total_value"] or 1.0
    positions = positions_of(brain)

    # Concentration.
    for p in positions:
        w = p["market_value"] / total
        if w > 0.20 and p["asset_class"] != "cash":
            out.append({"kind": "concentration", "priority": 2, "confidence": 0.8,
                        "title": f"{p['instrument']} is {w:.0%} of the portfolio",
                        "detail": f"Single-name concentration in {p['instrument']} ({w:.0%}); consider trimming.",
                        "payload": {"instrument": p["instrument"], "weight": round(w, 4)}})

    # Harvestable losses.
    losers = [p for p in positions if p["unrealised_gain"] < -500]
    if losers:
        tot = sum(p["unrealised_gain"] for p in losers)
        out.append({"kind": "loss_harvest", "priority": 2, "confidence": 0.78,
                    "title": f"Harvest ~${-tot:,.0f} of losses",
                    "detail": f"{len(losers)} position(s) in a loss could offset realised gains.",
                    "payload": {"candidates": [l["instrument"] for l in losers], "total_loss": round(tot, 2)}})

    # Idle cash drag.
    cash = brain["totals"]["by_asset_class"].get("cash", 0)
    if cash / total > 0.15 and cash > 20000:
        out.append({"kind": "idle_cash", "priority": 3, "confidence": 0.7,
                    "title": f"${cash:,.0f} cash ({cash/total:.0%}) is uninvested",
                    "detail": "Cash drag above 15% of the portfolio; consider deploying to target.",
                    "payload": {"cash": round(cash, 2)}})

    # Goal off-track.
    for g in goal_projections(brain):
        if not g["on_track"]:
            out.append({"kind": "goal_gap", "priority": 2, "confidence": 0.72,
                        "title": f"Goal off-track: {g['name']}",
                        "detail": f"{g['name']} has a {g['probability']:.0%} probability of success — review funding.",
                        "payload": {"goal": g["name"], "probability": g["probability"]}})

    # Intergenerational moment.
    nextgen = [p for p in brain.get("persons", []) if p.get("is_next_gen")]
    if nextgen:
        n = nextgen[0]
        out.append({"kind": "intergenerational", "priority": 3, "confidence": 0.7,
                    "title": f"Engage next-gen heir: {n.get('preferred_name') or n['full_name']}",
                    "detail": "A next-gen family member is in this household — engage early to retain the relationship.",
                    "payload": {"person_id": n["id"]}})

    return out


def goal_projections(brain: dict) -> list[dict]:
    """Monte-Carlo goal projections for a household's goals."""
    total = brain["totals"]["total_value"]
    allocation = brain["totals"]["by_asset_class"]
    goals = brain.get("goals", [])
    out = []
    for g in goals:
        a = g.get("assumptions") or {}
        proj = planning.project_goal(
            current_value=total * a.get("funding_share", 1.0 / max(len(goals), 1)),
            allocation=allocation, annual_contribution=a.get("annual_contribution", 0),
            annual_withdrawal=a.get("annual_withdrawal", 0), years=a.get("years", 15),
            target_amount=g["target_amount"],
        )
        out.append({"name": g["name"], "kind": g["kind"], "target_amount": g["target_amount"],
                    "on_track": proj.on_track, "probability": proj.probability_of_success,
                    "projected_median": proj.projected_median})
    return out


def life_events(brain: dict) -> list[str]:
    events = []
    for p in brain.get("persons", []):
        stage = (p.get("profile") or {}).get("life_stage")
        if stage:
            events.append(f"{p.get('preferred_name') or p['full_name']}: {stage.replace('-', ' ')}")
        if p.get("is_next_gen"):
            events.append(f"{p.get('preferred_name') or p['full_name']} is a next-generation family member")
    return events
