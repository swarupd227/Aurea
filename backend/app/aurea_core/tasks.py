"""Follow-ups: what the Tasks page and the conversation both read and change.

A task comes from an agent (Meeting Companion, on approval), from a client's request in Canvas, or
from a person in a conversation. Everything here is firm-scoped: a task, a household or an assignee
from another firm reads as not found.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import Task
from app.models.enums import TaskStatus, UserRole
from app.models.graph import Household
from app.models.identity import User

STATUSES = ("open", "done", "dismissed", "all")
LIST_LIMIT = 200
TITLE_MAX = 280
NOT_ASSIGNABLE = (UserRole.CLIENT, UserRole.ADMIN, UserRole.SUPERADMIN)


class TaskError(Exception):
    """A task change that cannot be made, in words a person can act on."""


def overdue(t: Task, today: date) -> bool:
    return str(t.status) == TaskStatus.OPEN.value and t.due_date is not None and t.due_date < today


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, status: str = "open",
                   assigned_to: uuid.UUID | None = None, household_id: uuid.UUID | None = None) -> list[Task]:
    """Newest 200 for the filter, then open first, soonest due first, undated after dated."""
    stmt = select(Task).where(Task.firm_id == firm_id)
    if status != "all":
        stmt = stmt.where(Task.status == status)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to == assigned_to)
    if household_id:
        stmt = stmt.where(Task.household_id == household_id)
    rows = (await session.execute(stmt.order_by(Task.created_at.desc()).limit(LIST_LIMIT))).scalars().all()
    return sorted(rows, key=lambda t: (str(t.status) != TaskStatus.OPEN.value, t.due_date or date.max))


async def views(session: AsyncSession, rows: list[Task], today: date) -> list[dict]:
    ids = list({t.assigned_to for t in rows if t.assigned_to})
    names: dict[uuid.UUID, str] = {}
    if ids:
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars().all():
            names[u.id] = u.full_name or u.email
    return [{
        "id": str(t.id), "title": t.title, "detail": t.detail, "status": str(t.status),
        "due_date": t.due_date.isoformat() if t.due_date else None, "overdue": overdue(t, today),
        "source": t.source, "subject_label": t.subject_label,
        "household_id": str(t.household_id) if t.household_id else None,
        "assigned_to": str(t.assigned_to) if t.assigned_to else None,
        "assigned_to_name": names.get(t.assigned_to) if t.assigned_to else None,
        "assigned_by": str(t.assigned_by) if t.assigned_by else None,
    } for t in rows]


async def get(session: AsyncSession, firm_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    t = await session.get(Task, task_id)
    if t is None or t.firm_id != firm_id:
        raise TaskError(f"Task {task_id} not found or access denied")
    return t


async def staff(session: AsyncSession, firm_id: uuid.UUID) -> list[User]:
    """Who a task can be given to: the firm's active staff, and not the accounts that only run the
    platform. A follow-up is client work, and those accounts hold none."""
    rows = (await session.execute(
        select(User).where(User.firm_id == firm_id, User.role.notin_(NOT_ASSIGNABLE), User.is_active.is_(True))
        .order_by(User.full_name))).scalars().all()
    return list(rows)


async def staff_member(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID) -> User:
    for u in await staff(session, firm_id):
        if u.id == user_id:
            return u
    raise TaskError(f"User {user_id} is not active staff at this firm")


async def staff_named(session: AsyncSession, firm_id: uuid.UUID, name: str) -> User:
    """One colleague from a name in a sentence. Exact wins; otherwise a part of the name must
    point at exactly one person. Never a guess between two."""
    people = await staff(session, firm_id)
    wanted = name.strip().lower()
    exact = [u for u in people if (u.full_name or "").lower() == wanted]
    matches = exact or [u for u in people if wanted and wanted in (u.full_name or "").lower()]
    if len(matches) == 1:
        return matches[0]
    pool = matches or people
    label = "matches more than one person" if matches else "matches nobody on the staff"
    raise TaskError(f"'{name}' {label}: {', '.join(u.full_name for u in pool)}.")


def complete(t: Task) -> None:
    if str(t.status) != TaskStatus.OPEN.value:
        raise TaskError(f"'{t.title}' is already {t.status}.")
    t.status = TaskStatus.DONE


def assign(t: Task, to: User | None, by: uuid.UUID) -> None:
    if str(t.status) != TaskStatus.OPEN.value:
        raise TaskError(f"'{t.title}' is already {t.status}.")
    t.assigned_to = to.id if to else None
    t.assigned_by = by if to else None


def new(firm_id: uuid.UUID, title: str, *, household: Household | None, due_date: date | None,
        assignee: User | None, by: uuid.UUID) -> Task:
    title = title.strip()
    if not title:
        raise TaskError("A task needs a title.")
    if len(title) > TITLE_MAX:
        raise TaskError(f"A task title is at most {TITLE_MAX} characters.")
    return Task(firm_id=firm_id, household_id=household.id if household else None, title=title,
                status=TaskStatus.OPEN, due_date=due_date, source="conversation",
                subject_label=household.name if household else None,
                assigned_to=assignee.id if assignee else None, assigned_by=by if assignee else None)
