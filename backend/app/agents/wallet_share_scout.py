"""Wallet Share Scout — surfaces held-away consolidation opportunities with personalised talking points.

'Held-away assets are the primary organic growth target' — L100 §5.1.
Share of wallet = managed AUM / (managed AUM + held-away). The agent scans the book for
households with significant held-away exposure and generates personalised consolidation
talking-point memos, calibrated to each household's life stage, goals, and estate picture.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.analytics import assumptions as A
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier

_MIN_HELD_AWAY = 100_000   # only surface meaningful consolidation opportunities
_MIN_WALLET_SHARE_GAP = 0.20  # flag when wallet share is below 80%


def _held_away_for_brain(brain: dict) -> float:
    persons = brain.get("persons", [])
    explicit = sum((p.get("profile") or {}).get("held_away", 0) or 0 for p in persons)
    if explicit:
        return explicit
    # Estimate from segment default wallet share if no explicit data
    aum = brain["totals"].get("total_value", 0) or 0
    seg = brain["household"].get("segment", "private_wealth")
    default_share = A.wallet_share_default(seg) or 0.6
    if default_share >= 1.0:
        return 0.0
    return max(aum / default_share - aum, 0.0)


class WalletShareScoutAgent(BaseAgent):
    key = AgentKey.WALLET_SHARE_SCOUT
    name = "Wallet Share Scout"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
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
            aum = brain["totals"].get("total_value", 0) or 0
            held_away = _held_away_for_brain(brain)

            if held_away < _MIN_HELD_AWAY:
                continue

            total = aum + held_away
            wallet_share = aum / total if total else 1.0
            if wallet_share > (1.0 - _MIN_WALLET_SHARE_GAP):
                continue  # already well-consolidated

            jurisdiction = (hh.get("values") or {}).get("jurisdiction") or ctx.firm.jurisdiction or "NZ"
            currency = {"US": "USD", "UK": "GBP"}.get(jurisdiction, "NZD")

            # Life-stage context for personalised talking points
            persons = brain.get("persons", [])
            goals = brain.get("goals", [])
            goal_kinds = [g.get("kind", "") for g in goals]
            has_retirement = any("retire" in k for k in goal_kinds)
            has_legacy = any("legacy" in k for k in goal_kinds)

            goal_context = ""
            if has_retirement:
                goal_context += "This household has an active retirement goal — "
                goal_context += "consolidation enables integrated decumulation planning. "
            if has_legacy:
                goal_context += "Legacy/bequest goals are in scope — "
                goal_context += "unified estate planning requires full asset visibility. "

            prompt = (
                f"Generate a personalised consolidation talking-point memo for the adviser. "
                f"Household: {hh['name']} | Jurisdiction: {jurisdiction}\n"
                f"Managed AUM: {currency} {aum:,.0f}\n"
                f"Estimated held-away: {currency} {held_away:,.0f}\n"
                f"Wallet share: {wallet_share:.0%}\n"
                f"Consolidation opportunity: {currency} {held_away:,.0f}\n"
                f"{goal_context}\n"
                "Write 3–4 bullet talking points the adviser can use in the next review meeting. "
                "Each bullet should be specific to this household's situation — not generic. "
                "Cover: why consolidation makes sense NOW for this client, what they gain "
                "(integrated planning, tax optimisation, simplified reporting), and one specific "
                "conversation opener tailored to their goals. Professional, concise."
            )
            fallback = (
                f"Wallet share opportunity — {hh['name']}: "
                f"{currency} {held_away:,.0f} held away ({wallet_share:.0%} consolidated). "
                f"Consolidation would increase managed AUM by {held_away / aum:.0%}. "
                f"Raise at next review: integrated planning across all assets enables "
                f"better tax, estate, and goal outcomes."
            )

            memo = await narrate(
                ctx, task="wallet_share_memo", system=firm_voice(ctx),
                prompt=prompt, fallback=fallback, max_tokens=450,
            )

            priority = 1 if wallet_share < 0.4 else 2
            drafts.append(RecommendationDraft(
                title=f"Consolidation opportunity — {currency} {held_away:,.0f} held away — {hh['name']}",
                summary=(
                    f"Wallet share {wallet_share:.0%}. "
                    f"{currency} {held_away:,.0f} estimated at external custodians."
                ),
                rationale=memo,
                confidence=0.75,
                priority=priority,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={
                    "signal": "wallet_share",
                    "aum": aum,
                    "held_away": held_away,
                    "wallet_share": wallet_share,
                    "consolidation_opportunity": held_away,
                    "jurisdiction": jurisdiction,
                },
                evidence={"wallet_share": round(wallet_share, 3), "held_away": held_away},
            ))

        # Sort by consolidation opportunity descending
        drafts.sort(key=lambda d: -(d.payload or {}).get("held_away", 0))
        return drafts
