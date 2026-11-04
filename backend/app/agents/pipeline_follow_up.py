"""Pipeline Follow-up Agent. Tier 1 · Scheduled each weekday morning.

Raises a pipeline deal that has gone quiet: no call, email, meeting or note logged against it or its contact for longer
than its stage's working target (`aurea_core/pipeline.QUIET_DAYS`: three weeks a lead, two qualified, ten days a
proposal). What a deal is, its stage and when it was last touched come from `pipeline.overview`, so the proposal and
the Growth pipeline page never disagree. A deal someone has since worked, moved or closed drops its proposal
(`expires_unraised`).

Approving gives the follow-up to the contact's owner: a task naming the prospect and the next step for the stage, due
today. Nothing else changes: the agent moves no stage, logs no touch and writes to no prospect.
"""
from __future__ import annotations

import uuid

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.models.enums import AgentKey, AutonomyTier


class PipelineFollowUpAgent(BaseAgent):
    key = AgentKey.PIPELINE_FOLLOW_UP
    name = "Pipeline Follow-up"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    # A deal worked since, moved on or closed is no longer quiet; its proposal goes.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        from app.aurea_core import pipeline

        funnel = await pipeline.overview(ctx.session, ctx.firm, None)   # the firm's: every owner's quiet deals
        return {"open": funnel["open"]["count"], "n_quiet": len(funnel["quiet"]), "quiet": funnel["quiet"]}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        from app.aurea_core import markets
        from app.aurea_core.pipeline import STAGE_LABEL

        say = markets.money_for(ctx.firm)
        drafts = []
        for d in sensed["quiet"]:
            q = d["quiet"]
            stage = STAGE_LABEL[d["stage"]]
            who = d["contact"] or d["title"]
            facts = (f"prospect {who}; deal '{d['title']}' at {stage.lower()}; nothing logged for {q['days']:.0f} days against a "
                     f"{q['limit']}-day target for the stage; owner {d['owner'] or 'none recorded'}; next step: {d['next_step']}"
                     + (f"; estimated assets {say(d['estimated_aum'])}" if d["estimated_aum"] else "; no estimate of assets"))
            fallback = (f"Nothing has been logged with {who} for {q['days']:.0f} days; the target at {stage.lower()} is {q['limit']}. "
                        f"Next: {d['next_step'][0].lower() + d['next_step'][1:]}.")
            note = await narrate(
                ctx, task="client_comms", system=firm_voice(ctx), fallback=fallback,
                prompt=("Write two plain sentences about a prospect who has gone quiet in the pipeline, for the adviser who owns them. "
                        "Do not address the reader as 'you'; do not guess why it went quiet or what the prospect wants. "
                        f"Use only these facts and add none: {facts}."))
            drafts.append(RecommendationDraft(
                title=f"Gone quiet — {who}: {stage.lower()} ({q['days']:.0f}d since last contact)",
                summary=f"{stage}, nothing logged for {q['days']:.0f} days against {q['limit']}. Owner: {d['owner'] or 'none recorded'}.",
                rationale=note,
                confidence=0.95,
                priority=1 if d["stage"] == "proposal" else 2,
                subject=Subject("crm_opportunity", uuid.UUID(d["id"]), who),
                payload={"stage": d["stage"], "stage_label": stage, "days_quiet": q["days"], "target_days": q["limit"],
                         "days_over": q["over"], "last_touch": d["last_touch"], "owner": d["owner"], "next_step": d["next_step"],
                         "estimated_aum": d["estimated_aum"], "contact_id": d["contact_id"]},
                evidence={"last_touch": d["last_touch"], "stage": d["stage"], "target_days": q["limit"]},
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """A task for the contact's owner, due today. It changes nothing in the pipeline."""
        from app.aurea_core import firm_time, tasks
        from app.models.crm import CrmContact, CrmOpportunity
        from app.models.identity import User

        p = recommendation.modified_payload or recommendation.payload or {}
        opp = await ctx.session.get(CrmOpportunity, recommendation.subject_id) if recommendation.subject_id else None
        contact = await ctx.session.get(CrmContact, opp.contact_id) if opp else None
        owner = await ctx.session.get(User, contact.owner_user_id) if contact and contact.owner_user_id else None
        title = f"Follow up {recommendation.subject_label}: {p.get('next_step', 'get back in touch')}"
        task = tasks.new(ctx.firm.id, title[:tasks.TITLE_MAX], household=None, due_date=firm_time.today(ctx.firm),
                         assignee=owner if owner and owner.is_active else None, by=recommendation.decided_by)
        task.source = str(self.key.value)
        task.subject_label = recommendation.subject_label
        ctx.session.add(task)
        await ctx.session.flush()
        return {"executed": True, "task_id": str(task.id),
                "note": f"Task added for {owner.full_name if owner else 'whoever picks it up; the contact has no owner'}."}
