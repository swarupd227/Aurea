"""Protection planner - reads each household's protection result and puts a measured gap to a person.

One proposal per household, kept up to date while the gap stands and closed when it clears. A household
that cannot be assessed raises nothing: "not assessed" is the result, and nothing here turns it into a gap.

A gap is a standing fact, not an event, so re-raising it every month would only teach people to ignore it.
Once a person has decided on one, it comes back when it has grown or after six months. A run someone asks
for on one household always says what it finds.

It never buys or recommends cover. Approving adds a task to review the cover with the client, because that
is a conversation for an adviser to have, and rolling back takes the task away.
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from sqlalchemy import select

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time, markets, protection
from app.core.db import utcnow
from app.models.engagement import Task
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus, TaskStatus
from app.models.governance import Recommendation

REVISIT_DAYS = 180
GROWTH = 1.10           # a gap at least this much larger than the one decided on comes back
TASK_DUE_DAYS = 30
SOURCE = AgentKey.PROTECTION_PLANNING.value
_SCENARIO_WORDS = {"death": "if they die", "disability": "if they cannot work", "care": "if they need care"}
_DECIDED = (RecommendationStatus.APPROVED, RecommendationStatus.MODIFIED, RecommendationStatus.EXECUTED,
            RecommendationStatus.DISMISSED)


async def _last_decisions(session, firm_id: uuid.UUID) -> dict[uuid.UUID, Recommendation]:
    """The newest decision a person took on each household's gap. Rolled back or expired is no decision."""
    rows = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key == SOURCE,
                                     Recommendation.status.in_(_DECIDED), Recommendation.decided_at.isnot(None))
        .order_by(Recommendation.decided_at.desc()))).scalars().all()
    latest: dict[uuid.UUID, Recommendation] = {}
    for rec in rows:
        latest.setdefault(rec.subject_id, rec)
    return latest


def _settled(rec: Recommendation | None, largest: float, now) -> bool:
    """Whether a person has already decided on a gap this size, recently enough to leave it alone."""
    if rec is None:
        return False
    decided = rec.decided_at if rec.decided_at.tzinfo else rec.decided_at.replace(tzinfo=timezone.utc)
    return now - decided < timedelta(days=REVISIT_DAYS) and largest < float((rec.payload or {}).get("largest_gap") or 0) * GROWTH


class ProtectionPlanningAgent(BaseAgent):
    key = AgentKey.PROTECTION_PLANNING
    name = "Protection Planner"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    # One proposal per household while a gap stands; a scan that no longer finds it closes it.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        today = firm_time.today(ctx.firm)
        if ctx.subject.type == "household" and ctx.subject.id:
            view = await protection.for_household(ctx.session, ctx.subject.id, firm_id=ctx.firm.id, today=today)
            views, decided = ([view] if view else []), {}
        else:
            views = (await protection.for_firm(ctx.session, ctx.firm.id, today=today))["households"]
            decided = await _last_decisions(ctx.session, ctx.firm.id)
        now, rows = utcnow(), []
        for view in views:
            gaps = protection.gaps_of(view)
            household_id = uuid.UUID(view["household_id"])
            if gaps and not _settled(decided.get(household_id), gaps[0]["gap"], now):
                rows.append({"household_id": view["household_id"], "household": view["household"], "gaps": gaps,
                             "policies": view["summary"]["policies_counted"], "debts_on_file": view["debts_on_file"],
                             "not_modelled": view["not_modelled"]})
        return {"rows": rows}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        say = markets.money_for(ctx.firm)
        drafts = []
        for row in sensed.get("rows") or []:
            gaps = row["gaps"]
            largest = gaps[0]
            caveats = []
            if any(g["partner_income_unknown"] for g in gaps):
                caveats.append("A partner's income is not on file, so the need on death is overstated.")
            if any(g["retirement_assumed"] for g in gaps):
                caveats.append("A retirement age is assumed, not stated by the client.")
            if not row["policies"]:
                caveats.append("No cover is on file, which does not mean the client has none.")
            if not row["debts_on_file"]:
                caveats.append("No debts are on file, which does not mean there are none.")
            said = "; ".join(f"{g['person']}: {say(g['gap'])} {_SCENARIO_WORDS[g['scenario']]}" for g in gaps)
            drafts.append(RecommendationDraft(
                # No figures in the title: the gap changes while the proposal stays the same one.
                title=f"Protection gap - {row['household']}",
                summary=said + ".",
                rationale=" ".join([
                    f"Worked from what is on file, after counting {say(largest['assets'])} of assets. Approving adds "
                    "a task to review this cover with the client. Nothing is bought, and no product is recommended.",
                    *caveats]),
                confidence=0.6 if caveats else 0.8,
                priority=2 if largest["gap"] * 2 > largest["need"] else 3,
                subject=Subject("household", uuid.UUID(row["household_id"]), row["household"]),
                payload={"signal": "protection_gap", "gaps": gaps, "largest_gap": largest["gap"],
                         "not_modelled": row["not_modelled"]},
                evidence={"gaps": len(gaps), "policies_counted": row["policies"], "debts_on_file": row["debts_on_file"]},
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        # A review with a client is a person's to take on. At Tier 3 the runtime calls act() with nobody
        # having decided, so the proposal stays open for one.
        if not recommendation.decided_by:
            return {"executed": False, "note": "A review is added only when a person approves; "
                                               "this proposal is waiting for that decision."}
        household_id = recommendation.subject_id
        title = f"Review protection with {recommendation.subject_label}"
        existing = (await ctx.session.execute(
            select(Task).where(Task.firm_id == ctx.firm.id, Task.household_id == household_id, Task.source == SOURCE,
                               Task.status == TaskStatus.OPEN).limit(1))).scalars().first()
        if existing:
            existing.detail = recommendation.summary
            await ctx.session.flush()
            return {"executed": True, "task_id": str(existing.id), "added": False,
                    "note": f"'{existing.title}' was already open, and now says what was found this time."}
        task = Task(firm_id=ctx.firm.id, household_id=household_id, title=title, detail=recommendation.summary,
                    status=TaskStatus.OPEN, source=SOURCE, subject_label=recommendation.subject_label,
                    due_date=firm_time.today(ctx.firm) + timedelta(days=TASK_DUE_DAYS),
                    assigned_to=recommendation.decided_by, assigned_by=recommendation.decided_by)
        ctx.session.add(task)
        await ctx.session.flush()
        return {"executed": True, "task_id": str(task.id), "added": True, "note": f"Added '{title}'."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        result = (recommendation.payload or {}).get("execution_result") or {}
        if not result.get("task_id") or not result.get("added"):
            return {"reversed": False, "note": "This decision added no task."}
        task = await ctx.session.get(Task, uuid.UUID(result["task_id"]))
        if task is None or str(task.status) != TaskStatus.OPEN.value:
            return {"reversed": False, "note": "The task has already been dealt with, so it stays."}
        task.status = TaskStatus.DISMISSED
        await ctx.session.flush()
        return {"reversed": True, "note": f"'{task.title}' was taken off the list."}
