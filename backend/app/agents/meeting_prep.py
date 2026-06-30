"""Meeting preparation agent (spec Table 9) — deep. Tier 1 — adviser owns the conversation.

Assembles a structured pre-meeting brief from the client brain: portfolio snapshot, goals
('am I on track?'), watch-items (concentration, harvestable losses, off-track goals), life
events, relevant house views, and a suggested agenda. The brief is saved on the meeting."""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.agents._signals import book_signals, goal_projections, life_events
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import knowledge
from app.aurea_core.graph import household_brain
from app.models.engagement import Meeting
from app.models.enums import AgentKey, AutonomyTier, MeetingStatus


class MeetingPrepAgent(BaseAgent):
    key = AgentKey.MEETING_PREP
    name = "Meeting Preparation"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1

    async def _resolve(self, ctx: AgentContext):
        """Subject may be a meeting or a household. Returns (meeting | None, household_id)."""
        if ctx.subject.type == "meeting" and ctx.subject.id:
            meeting = await ctx.session.get(Meeting, ctx.subject.id)
            return meeting, (meeting.household_id if meeting else None)
        return None, ctx.subject.id

    async def sense(self, ctx: AgentContext) -> dict:
        meeting, household_id = await self._resolve(ctx)
        if not household_id:
            return {"applicable": False}
        brain = await household_brain(ctx.session, household_id)
        if not brain:
            return {"applicable": False}
        citations = await knowledge.retrieve(ctx.session, ctx.firm.id, "house view outlook positioning", k=2)
        return {"applicable": True, "meeting_id": str(meeting.id) if meeting else None,
                "meeting_title": meeting.title if meeting else f"Review — {brain['household']['name']}",
                "household_id": str(household_id), "brain": brain, "citations": citations}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        brain = sensed["brain"]
        hh = brain["household"]
        totals = brain["totals"]
        goals = goal_projections(brain)
        signals = book_signals(brain)
        events = life_events(brain)
        cites = sensed["citations"]

        agenda = self._agenda(goals, signals, events)
        brief = {
            "portfolio": {"total_value": totals["total_value"], "by_asset_class": totals["by_asset_class"],
                          "data_confidence": totals["data_confidence"]},
            "goals": goals,
            "watch_items": [{"title": s["title"], "detail": s["detail"]} for s in signals],
            "life_events": events,
            "house_views": [{"title": c["title"], "excerpt": c["excerpt"][:200]} for c in cites],
            "agenda": agenda,
        }

        # Persist the brief on the meeting.
        if sensed.get("meeting_id"):
            meeting = await ctx.session.get(Meeting, sensed["meeting_id"])
            if meeting:
                meeting.brief = brief
                if meeting.status == MeetingStatus.SCHEDULED:
                    meeting.status = MeetingStatus.PREPPED
                await ctx.session.flush()

        on_track = sum(1 for g in goals if g["on_track"])
        fallback = (
            f"Pre-meeting brief for {hh['name']}: portfolio ${totals['total_value']:,.0f}, "
            f"{on_track}/{len(goals)} goals on track, {len(signals)} watch-item(s). Suggested agenda: "
            + "; ".join(agenda) + "."
        )
        prompt = (
            f"Write a 2-3 sentence opening summary for an adviser's pre-meeting brief for household "
            f"'{hh['name']}'. Portfolio ${totals['total_value']:,.0f}; {on_track}/{len(goals)} goals on "
            f"track; watch-items {[s['title'] for s in signals]}; house views {[c['title'] for c in cites]}. "
            "Be warm, concise and specific."
        )
        rationale = await narrate(ctx, task="narrative", system=firm_voice(ctx), prompt=prompt, fallback=fallback)

        return [RecommendationDraft(
            title=f"Meeting brief — {sensed['meeting_title']}",
            summary=f"Portfolio ${totals['total_value']:,.0f} · {on_track}/{len(goals)} goals on track · "
                    f"{len(signals)} watch-item(s) · {len(agenda)} agenda topics.",
            rationale=rationale, confidence=0.85, priority=3,
            subject=Subject("household", sensed["household_id"], hh["name"]),
            payload={"brief": brief, "meeting_id": sensed.get("meeting_id")},
            evidence={"data_confidence": totals["data_confidence"]}, citations=cites,
        )]

    def _agenda(self, goals, signals, events) -> list[str]:
        agenda = ["Portfolio review & positioning vs the house view"]
        if any(not g["on_track"] for g in goals):
            agenda.append("Goals that need attention & funding options")
        else:
            agenda.append("Progress against goals — on track")
        for s in signals[:2]:
            agenda.append(s["title"])
        if events:
            agenda.append("Life events & next-gen engagement")
        return agenda

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        return {"executed": True, "note": "Brief acknowledged; the adviser leads the conversation."}
