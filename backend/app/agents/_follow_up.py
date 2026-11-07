"""What approving a proposal does for an agent whose finding is a person's to take forward (B-127).

These agents find something worth a conversation - an estate plan to review, money held away, a tax
position, a review overdue - and nothing in the platform can do that conversation for a person. Approving
used to stamp the proposal and do nothing else, so "approved" read as if something had happened.

Approving now adds one task, for the person who takes it forward: the household's adviser as recorded
(`aurea_core/advisers.py`, unassigned when nobody is), or compliance for the firm-wide conduct review.
It places no order, writes no figure, closes no flag and resumes no agent. A second approval for the same
household, while the first task is still open, brings that task up to date instead of adding another.
Rolling back takes the task off the list if it is still open; one already worked stays.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.engagement import Task
from app.models.enums import TaskStatus


class FollowUpOnApproval:
    """Mixed in ahead of BaseAgent: `class X(FollowUpOnApproval, BaseAgent)`."""

    # Who takes it forward: "adviser" (the household's) or "compliance" (the firm's, unassigned).
    follow_up_owner: str = "adviser"
    # What the task says to do, in front of the proposal's title.
    follow_up_verb: str = "Follow up"
    # What the person is to do, as the Confirm card says it.
    follow_up_what: str = "follow this up with the client"
    follow_up_days: int = 7
    # Anything approving must not be read as doing, said on the Confirm card.
    follow_up_caveat: str = "nothing on the book changes"

    def on_approval(self, rec) -> str:
        who = "compliance" if self.follow_up_owner == "compliance" else "the household's adviser"
        return f"A task for {who} to {self.follow_up_what}, due in {self.follow_up_days} days; {self.follow_up_caveat}"

    def _title(self, rec) -> str:
        from app.aurea_core import tasks

        return f"{self.follow_up_verb}: {rec.title or rec.subject_label or 'proposal'}"[: tasks.TITLE_MAX]

    async def act(self, ctx, recommendation) -> dict:
        from app.aurea_core import advisers, firm_time

        rec = recommendation
        source = str(self.key.value)
        household_id = rec.subject_id if (rec.subject_type == "household" and rec.subject_id) else None
        detail = rec.summary
        due = firm_time.today(ctx.firm) + timedelta(days=self.follow_up_days)

        existing = (await ctx.session.execute(
            select(Task).where(Task.firm_id == ctx.firm.id, Task.source == source, Task.status == TaskStatus.OPEN,
                               Task.household_id == household_id if household_id else Task.household_id.is_(None))
            .limit(1))).scalars().first()
        if existing:
            existing.title = self._title(rec)
            existing.detail = detail
            await ctx.session.flush()
            return {"executed": True, "task_id": str(existing.id), "added": False,
                    "note": f"'{existing.title}' was already open, and now says what was found this time."}

        assignee = None
        if self.follow_up_owner == "adviser" and household_id:
            adviser = await advisers.adviser_of(ctx.session, ctx.firm.id, household_id)
            assignee = adviser if adviser is not None and adviser.is_active else None
        task = Task(firm_id=ctx.firm.id, household_id=household_id, title=self._title(rec), detail=detail,
                    status=TaskStatus.OPEN, source=source, subject_label=rec.subject_label, due_date=due,
                    assigned_to=assignee.id if assignee else None,
                    assigned_by=rec.decided_by if assignee else None)
        ctx.session.add(task)
        await ctx.session.flush()
        if self.follow_up_owner == "compliance":
            for_whom = "compliance"
        elif assignee is not None:
            for_whom = assignee.full_name
        else:
            for_whom = "whoever picks it up; no adviser is recorded for this household"
        return {"executed": True, "task_id": str(task.id), "added": True,
                "note": f"Added '{task.title}' for {for_whom}, due {due.isoformat()}."}

    async def rollback(self, ctx, recommendation) -> dict:
        result = (recommendation.payload or {}).get("execution_result") or {}
        if not result.get("task_id") or not result.get("added"):
            return {"reversed": False, "note": "This decision added no task."}
        task = await ctx.session.get(Task, uuid.UUID(result["task_id"]))
        if task is None or str(task.status) != TaskStatus.OPEN.value:
            return {"reversed": False, "note": "The task has already been dealt with, so it stays."}
        task.status = TaskStatus.DISMISSED
        await ctx.session.flush()
        return {"reversed": True, "note": f"'{task.title}' was taken off the list."}
