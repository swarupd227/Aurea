"""Follow-ups: what the Tasks page and the conversation both read and change.

A task comes from an agent (Meeting Companion, on approval), from a client's request in Canvas, from a
person in a conversation, or from compliance or a branch leader handing a proposal to its client's adviser. Everything here is firm-scoped: a task, a household or an assignee
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
# Compliance and a branch leader may not approve a trade, but may hand it to the client's adviser to decide.
REFERRERS = (UserRole.COMPLIANCE, UserRole.BRANCH_LEADER)
REFERRAL_SOURCE = "referral"


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


async def prepare_referral(session: AsyncSession, firm_id: uuid.UUID, rec, *, reason: str | None, by: User,
                           due_date: date | None = None) -> dict:
    """Handing an open proposal to the client's recorded adviser as a task, with the referrer's reason. It goes
    to whoever is on record (advisers.adviser_of), never someone worked out; it decides nothing and changes
    nothing on the proposal. Writes nothing."""
    from app.aurea_core import advisers

    if UserRole(by.role) not in REFERRERS:
        raise TaskError("Handing a proposal to its adviser is for compliance or a branch leader.")
    if rec is None or rec.firm_id != firm_id:
        raise TaskError("That is not a proposal on file here. Look it up again and use its id.")
    if str(rec.status) != "proposed":
        raise TaskError(f"'{rec.title}' is already {rec.status}, so there is nothing left for an adviser to decide.")
    reason = (reason or "").strip()
    if not reason:
        raise TaskError("Say why it goes to the adviser: the reason is what they read.")
    household_id = (await advisers.subject_households(session, firm_id, [rec])).get(rec.id)
    household = await session.get(Household, household_id) if household_id else None
    if household is None:
        raise TaskError(f"'{rec.title}' is not about one client, so it has no adviser to go to.")
    adviser = await advisers.adviser_of(session, firm_id, household.id)
    if adviser is None or not adviser.is_active:
        raise TaskError(f"{household.name} has no adviser on record, so there is nobody to hand it to.")
    title = f"Decide the proposal: {rec.title}"[:TITLE_MAX]
    waiting = (await session.execute(select(Task).where(
        Task.firm_id == firm_id, Task.household_id == household.id, Task.title == title,
        Task.source == REFERRAL_SOURCE, Task.status == TaskStatus.OPEN))).scalars().first()
    if waiting is not None:
        raise TaskError(f"{adviser.full_name} already has this proposal to decide, as an open task.")
    who = UserRole(by.role).value.replace("_", " ")
    return {"proposal": rec, "household": household, "adviser": adviser, "title": title, "due_date": due_date,
            "detail": f"From {by.full_name} ({who}): {reason}"}


def referral(firm_id: uuid.UUID, p: dict, by: uuid.UUID) -> Task:
    return Task(firm_id=firm_id, household_id=p["household"].id, title=p["title"], detail=p["detail"],
                status=TaskStatus.OPEN, due_date=p["due_date"], source=REFERRAL_SOURCE,
                subject_label=p["household"].name, assigned_to=p["adviser"].id, assigned_by=by)


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
