"""Conduct surveillance agent (spec Table 11). Tier 2/3 — supervises the other agents.

Per-recommendation review runs automatically inside the runtime (app.provenance.surveillance).
When invoked as a run, this agent produces a supervisory summary over recent open flags."""
from __future__ import annotations

from sqlalchemy import select

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.models.enums import AgentKey, AutonomyTier, SurveillanceSeverity
from app.models.governance import SurveillanceFlag


class ConductSurveillanceAgent(BaseAgent):
    key = AgentKey.CONDUCT_SURVEILLANCE
    name = "Conduct Surveillance"
    lifecycle_stage = "protect_govern"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        flags = (
            await ctx.session.execute(
                select(SurveillanceFlag).where(
                    SurveillanceFlag.firm_id == ctx.firm.id, SurveillanceFlag.resolved.is_(False)
                )
            )
        ).scalars().all()
        return {
            "open_flags": len(flags),
            "high": sum(1 for f in flags if f.severity == SurveillanceSeverity.HIGH),
            "by_category": _count_by(flags, "category"),
            "auto_paused": sum(1 for f in flags if f.auto_paused_agent),
        }

    async def think(self, ctx, sensed) -> list[RecommendationDraft]:
        if sensed["open_flags"] == 0:
            return []
        summary = (
            f"{sensed['open_flags']} open conduct flag(s): {sensed['high']} high-severity, "
            f"{sensed['auto_paused']} triggering an automatic agent pause. "
            f"By category: {sensed['by_category']}."
        )
        return [RecommendationDraft(
            title="Conduct surveillance review",
            summary=summary,
            rationale="Compliance should review open conduct flags. High-severity items have "
                      "auto-paused the originating agent pending review (kill-switch).",
            confidence=0.9, priority=1 if sensed["high"] else 3,
            subject=Subject("firm", ctx.firm.id, ctx.firm.name),
            payload=sensed, evidence={"open_flags": sensed["open_flags"]},
        )]


def _count_by(items, attr) -> dict:
    out: dict = {}
    for i in items:
        k = getattr(i, attr)
        out[k] = out.get(k, 0) + 1
    return out
