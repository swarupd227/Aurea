"""Glide Path Advisor — recommends equity step-down for households approaching a major goal.

'Glide path: a pre-set schedule of decreasing equity exposure as the goal date approaches'
— L100 §8.2. Sequence-of-returns risk is highest in the 3–5 years before and after retirement.
Flags households that are over-risked for their timeline and proposes a de-risking allocation.
"""
from __future__ import annotations

from datetime import date

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier

_WINDOW_YEARS = 7     # flag households within this many years of a major goal
_MIN_EQUITY_PCT = 0.20  # floor: never recommend below 20% equity


def _target_equity(years_to_goal: float) -> float:
    """Simple glide path rule: 100 - age heuristic adapted to years-to-goal."""
    if years_to_goal <= 0:
        return _MIN_EQUITY_PCT
    if years_to_goal >= 20:
        return 0.80
    # Linear from 80% at 20y to 20% at 0y
    return max(_MIN_EQUITY_PCT, 0.20 + (years_to_goal / 20) * 0.60)


def _current_equity_pct(brain: dict) -> float:
    totals = brain.get("totals", {})
    by_class = totals.get("by_asset_class") or {}
    total = totals.get("total_value") or 0
    if not total:
        return 0.0
    equity = by_class.get("equity", 0) + by_class.get("multi_asset", 0) * 0.5
    return equity / total


def _years_to_goal(goal: dict) -> float | None:
    target_date = goal.get("target_date")
    if not target_date:
        return None
    try:
        y, m, d = map(int, target_date[:10].split("-"))
        target = date(y, m, d)
        delta = (target - date.today()).days / 365.25
        return delta
    except (ValueError, TypeError):
        return None


class GlidePathAgent(BaseAgent):
    key = AgentKey.GLIDE_PATH
    name = "Glide Path Advisor"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            ids = [h["id"] for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [str(ctx.subject.id)]
        brains = [b for hid in ids if (b := await household_brain(s, hid))]
        return {"brains": brains}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for brain in sensed["brains"]:
            hh = brain["household"]
            goals = brain.get("goals", [])
            totals = brain.get("totals", {})
            total_value = totals.get("total_value", 0) or 0

            # Find the most urgent retirement/major goal within the window
            target_goal = None
            years_remaining = None
            for g in goals:
                kind = g.get("kind", "")
                if kind not in ("retirement", "education", "major_purchase", "legacy"):
                    kind_lower = kind.lower()
                    if "retire" not in kind_lower and "goal" not in kind_lower:
                        continue
                yrs = _years_to_goal(g)
                if yrs is None:
                    continue
                if 0 < yrs <= _WINDOW_YEARS:
                    if years_remaining is None or yrs < years_remaining:
                        target_goal = g
                        years_remaining = yrs

            if target_goal is None or years_remaining is None:
                continue

            current_equity = _current_equity_pct(brain)
            target_equity = _target_equity(years_remaining)
            equity_gap = current_equity - target_equity  # positive = over-risked

            if equity_gap < 0.05:  # within 5pp — no action needed
                continue

            jurisdiction = (hh.get("values") or {}).get("jurisdiction") or ctx.firm.jurisdiction or "NZ"
            currency = {"US": "USD", "UK": "GBP"}.get(jurisdiction, "NZD")

            # Compute year-by-year glide schedule
            schedule = []
            for yr in range(1, min(int(years_remaining) + 2, 8)):
                eq = _target_equity(years_remaining - yr)
                schedule.append(f"Year {yr}: {eq:.0%} equity")

            prompt = (
                f"Write a glide-path advisory memo for the adviser. "
                f"Household: {hh['name']} | Jurisdiction: {jurisdiction}\n"
                f"Portfolio: {currency} {total_value:,.0f}\n"
                f"Goal: {target_goal.get('kind', 'retirement')} in {years_remaining:.1f} years "
                f"(target {currency} {target_goal.get('target_amount', 0):,.0f})\n"
                f"Current equity allocation: {current_equity:.0%}\n"
                f"Recommended equity at this horizon: {target_equity:.0%}\n"
                f"Gap: {equity_gap:.0%} over-risked\n"
                f"Suggested glide schedule: {'; '.join(schedule)}\n\n"
                "In 3–4 sentences: explain the sequence-of-returns risk the household faces, "
                "quantify the de-risking required, and recommend the first step. "
                "Professional, specific to this client."
            )
            fallback = (
                f"Glide path alert — {hh['name']}: "
                f"{years_remaining:.1f} years to {target_goal.get('kind', 'retirement')} goal. "
                f"Current equity {current_equity:.0%} vs recommended {target_equity:.0%} "
                f"({equity_gap:.0%} over-risked). "
                f"Propose reducing equity by {equity_gap:.0%} over the next {int(years_remaining)} years."
            )

            memo = await narrate(
                ctx, task="glide_path_memo", system=firm_voice(ctx),
                prompt=prompt, fallback=fallback, max_tokens=400,
            )

            priority = 1 if equity_gap > 0.15 or years_remaining < 3 else 2
            drafts.append(RecommendationDraft(
                title=(
                    f"Glide path — {equity_gap:.0%} over-equity for "
                    f"{years_remaining:.0f}y horizon — {hh['name']}"
                ),
                summary=(
                    f"{target_goal.get('kind', 'Retirement')} goal in {years_remaining:.1f} years. "
                    f"Reduce equity from {current_equity:.0%} → {target_equity:.0%}."
                ),
                rationale=memo,
                confidence=0.88,
                priority=priority,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={
                    "signal": "glide_path",
                    "current_equity_pct": round(current_equity, 3),
                    "target_equity_pct": round(target_equity, 3),
                    "equity_gap": round(equity_gap, 3),
                    "years_remaining": round(years_remaining, 1),
                    "goal": target_goal.get("kind"),
                    "glide_schedule": schedule,
                    "jurisdiction": jurisdiction,
                },
                evidence={
                    "current_equity": round(current_equity, 3),
                    "target_equity": round(target_equity, 3),
                    "years_remaining": round(years_remaining, 1),
                },
            ))

        drafts.sort(key=lambda d: (d.priority, -(d.payload or {}).get("equity_gap", 0)))
        return drafts
