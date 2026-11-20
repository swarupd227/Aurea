"""Warm Introduction Agent (B-200). Tier 1 · Scheduled each Monday morning.

For each prospect still at lead or qualified with no meeting held yet, looks for a way in through people the firm
already knows (`aurea_core/introductions.paths`): a client, a centre of influence or a contact someone recorded as
knowing them, and the member of staff best placed to ask. One proposal per prospect, for the best path that scores
`MIN_SCORE` or more; a prospect met since, moved on, or with no path any longer drops its proposal.

Approving gives the ask to that member of staff: a task to ask the introducer for an introduction, due in a week. It
contacts nobody, logs nothing on the prospect and moves no stage; whether and how to ask is the person's.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.models.enums import AgentKey, AutonomyTier, TaskStatus

ASK_DAYS = 7


def path_words(path: dict) -> str:
    """The chain as a figure line: "Sophie Tran adviser to Wei Chen · Wei Chen knows James Whitaker"."""
    return " · ".join(f"{h['from']['name']} {h['label']} {h['to']['name']}" for h in path["hops"])


class WarmIntroductionAgent(BaseAgent):
    key = AgentKey.WARM_INTRODUCTION
    name = "Warm Introduction"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    # A prospect met since, moved on, or with no path any longer: its proposal goes.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        from app.aurea_core import firm_time, introductions

        scan = await introductions.open_prospects(ctx.session, ctx.firm)
        prospects = [{"contact_id": p["contact_id"], "name": p["name"], "stage": p["stage"], "path": p["best"]}
                     for p in scan["prospects"] if p["best"] and p["best"]["proposable"]]
        return {"not_yet_met": len(scan["prospects"]), "met": scan["met"], "with_a_way_in": len(prospects),
                "prospects": prospects, "as_of": firm_time.today(ctx.firm).isoformat()}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for p in sensed["prospects"]:
            path, who = p["path"], p["name"]
            introducer, ask = path["introducer"], path["ask"]
            chain = path_words(path)
            evidence = [h["evidence"] for h in path["hops"] if h.get("evidence")]
            facts = (f"prospect {who} at {p['stage']}; best way in scores {path['score']} of 100; the chain: {chain}; "
                     f"introducer {introducer['name']} ({', '.join(introducer['standing']) or 'no standing recorded'}); "
                     f"who should ask: {ask['name']}; evidence recorded: {'; '.join(evidence) or 'none beyond the client graph'}")
            fallback = (f"{introducer['name']} is the closest way in to {who}: {chain}. "
                        f"{ask['name']} is best placed to ask for the introduction.")
            note = await narrate(
                ctx, task="client_comms", system=firm_voice(ctx), fallback=fallback,
                prompt=("Write two plain sentences for a member of staff about who could introduce them to a prospect. "
                        "Do not address the reader as 'you', do not guess what the prospect wants or how close anyone is "
                        f"beyond what is stated, and suggest no wording for the ask. Use only these facts and add none: {facts}."))
            drafts.append(RecommendationDraft(
                title=f"Warm introduction — {who} through {introducer['name']}",
                summary=f"{chain}. Ask: {ask['name']}. Score {path['score']}/100.",
                rationale=note,
                confidence=min(0.95, 0.5 + path["score"] / 200),
                priority=2 if p["stage"] == "qualified" else 3,
                subject=Subject("crm_contact", uuid.UUID(p["contact_id"]), who),
                payload={"contact_id": p["contact_id"], "prospect": who, "stage": p["stage"], "path": path,
                         "introducer": introducer, "ask": ask, "score": path["score"]},
                evidence={"score": path["score"], "hops": len(path["hops"]), "recorded_links": path["recorded_links"]},
            ))
        return drafts

    def on_approval(self, rec) -> str:
        p = rec.modified_payload or rec.payload or {}
        return (f"A task for {(p.get('ask') or {}).get('name') or 'whoever picks it up'} to ask "
                f"{(p.get('introducer') or {}).get('name') or 'the introducer'} for an introduction, due in {ASK_DAYS} days; "
                "nobody is contacted and nothing is logged on the prospect")

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """A task for the member of staff best placed to ask. Nobody is contacted; the prospect's record is untouched."""
        from app.aurea_core import firm_time, tasks
        from app.models.engagement import Task
        from app.models.identity import User

        rec = recommendation
        p = rec.modified_payload or rec.payload or {}
        ask = p.get("ask") or {}
        introducer = (p.get("introducer") or {}).get("name") or "the introducer"
        user = await ctx.session.get(User, uuid.UUID(ask["id"])) if ask.get("id") else None
        user = user if user is not None and user.is_active and user.firm_id == ctx.firm.id else None
        title = f"Ask {introducer} to introduce {rec.subject_label}"[: tasks.TITLE_MAX]
        detail = path_words(p["path"]) if p.get("path") else rec.summary
        existing = (await ctx.session.execute(select(Task).where(
            Task.firm_id == ctx.firm.id, Task.source == self.key.value, Task.status == TaskStatus.OPEN,
            Task.subject_label == rec.subject_label).limit(1))).scalars().first()
        if existing:
            existing.title, existing.detail = title, detail
            existing.assigned_to = user.id if user else existing.assigned_to
            await ctx.session.flush()
            return {"executed": True, "task_id": str(existing.id), "added": False,
                    "note": f"'{existing.title}' was already open, and now names the best way in found this time."}
        task = tasks.new(ctx.firm.id, title, household=None, due_date=firm_time.today(ctx.firm) + timedelta(days=ASK_DAYS),
                         assignee=user, by=rec.decided_by)
        task.source, task.subject_label, task.detail = self.key.value, rec.subject_label, detail
        ctx.session.add(task)
        await ctx.session.flush()
        return {"executed": True, "task_id": str(task.id), "added": True,
                "note": f"Task added for {user.full_name if user else 'whoever picks it up'}: {title}."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        from app.models.engagement import Task

        result = (recommendation.payload or {}).get("execution_result") or {}
        if not result.get("task_id") or not result.get("added"):
            return {"reversed": False, "note": "This decision added no task."}
        task = await ctx.session.get(Task, uuid.UUID(result["task_id"]))
        if task is None or str(task.status) != TaskStatus.OPEN.value:
            return {"reversed": False, "note": "The task has already been dealt with, so it stays."}
        task.status = TaskStatus.DISMISSED
        await ctx.session.flush()
        return {"reversed": True, "note": f"'{task.title}' was taken off the list."}
