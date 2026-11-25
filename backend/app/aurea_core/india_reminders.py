"""A date that is near, or past, becomes a task for the person who owns it (India polish, B-247).

The compliance calendar and the countdown only showed dates. This reads the same items (`india_deadlines`, which reads
`compliance_calendar`, `governance` and the KYC records) and raises one task per item, once, for its owner:

  the firm's    a filing, the registration's expiry and a policy's review go to compliance (the compliance officer the firm
                recorded when a user carries that name, else the firm's first active compliance user, else nobody in particular)
  a client's    a periodic KYC and risk review coming up or past, a KYC the KRA holds or has rejected or that has run out, and a
                minor who is now an adult go to the household's recorded adviser (`advisers.adviser_of`), never someone
                worked out; a household with none gets an unassigned task that says so

It acts when an item is overdue or falls due within 14 days. An item with no day is not raised, and advance tax and the return
are not raised, because whether either applies to a client is not assessed here. One task per obligation and period: the title
carries both, so the task is found again by its title whatever its state, and a task a person closed is not raised a second time.
It changes no filing, KYC or guardianship record, tells no client, and does not say the firm is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, governance, india_deadlines, kyc_india, tasks
from app.models.engagement import Task
from app.models.enums import TaskStatus, UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

SOURCE = "india_reminder"
WITHIN_DAYS = 14
FIRM_KINDS = ("filing", "registration", "policy")
HOUSEHOLD_KINDS = ("kyc_review", "majority")
KYC_STOPS = ("overdue", "blocked")


async def compliance_owner(session: AsyncSession, firm: Firm) -> User | None:
    people = (await session.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.COMPLIANCE, User.is_active.is_(True))
                                    .order_by(User.full_name))).scalars().all()
    named = ((firm.settings or {}).get("sebi_registration") or {}).get("compliance_officer")
    if named:
        for u in people:
            if (u.full_name or "").strip().lower() == str(named).strip().lower():
                return u
    return people[0] if people else None


def _near(item: dict) -> bool:
    return item.get("due_on") is not None and item.get("days") is not None and item["state"] in ("overdue", "due_soon", "upcoming") \
        and item["days"] <= WITHIN_DAYS


async def _exists(session: AsyncSession, firm_id: uuid.UUID, title: str, household_id: uuid.UUID | None) -> bool:
    q = select(Task.id).where(Task.firm_id == firm_id, Task.source == SOURCE, Task.title == title)
    q = q.where(Task.household_id == household_id) if household_id else q.where(Task.household_id.is_(None))
    return (await session.execute(q.limit(1))).first() is not None


def _title(item: dict) -> str:
    return f"India: {item['label']}"[:tasks.TITLE_MAX]


def _detail(item: dict) -> str:
    when = f"overdue by {-item['days']} day(s)" if item["days"] < 0 else "due today" if item["days"] == 0 else f"due in {item['days']} day(s)"
    out = f"Due {item['due_on']} ({when}). Source: {item['source']}."
    if item.get("assumed"):
        out += f" Assumed: {item['assumed']}."
    return out


def _task(firm_id: uuid.UUID, item: dict, *, household: Household | None, owner: User | None, unassigned_note: str | None = None) -> Task:
    detail = _detail(item) + (f" {unassigned_note}" if owner is None and unassigned_note else "")
    return Task(firm_id=firm_id, household_id=household.id if household else None, title=_title(item), detail=detail, status=TaskStatus.OPEN,
                due_date=date.fromisoformat(item["due_on"]), source=SOURCE, subject_label=household.name if household else "The firm",
                assigned_to=owner.id if owner else None, assigned_by=None)


async def plan(session: AsyncSession, firm: Firm) -> dict:
    """What would be raised now, with who each goes to and whether a task for it already exists. Writes nothing."""
    today = firm_time.today(firm)
    if not governance.applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "items": [], "message": "Reminders are raised for a firm holding an Indian licence."}
    out: list[dict] = []
    owner = await compliance_owner(session, firm)
    for i in (await india_deadlines.firm_items(session, firm))["items"]:
        if i["kind"] in FIRM_KINDS and _near(i):
            out.append({"item": i, "household": None, "owner": owner, "scope": "firm",
                        "note": "No active compliance user is recorded, so nobody is named."})
    for h in await advisers._active(session, firm.id):
        adviser = await advisers.adviser_of(session, firm.id, h.id)
        adviser = adviser if adviser is not None and adviser.is_active else None
        note = "The household has no adviser on record: assign one."
        for i in (await india_deadlines.household_items(session, firm, h.id))["items"]:
            if i["kind"] in HOUSEHOLD_KINDS and _near(i):
                out.append({"item": i, "household": h, "owner": adviser, "scope": "household", "note": note})
        for p in (await kyc_india.overview(session, firm, household_id=h.id))["parties"]:
            if p["state"] in KYC_STOPS:
                # A blocked record has no date of its own (the KRA holds it today); an overdue one is due on the day it ran out.
                due = min(date.fromisoformat(p["due_on"]), today) if p.get("due_on") and p["state"] == "overdue" else today
                item = {"kind": "kyc", "label": f"KYC {p['state']} for {p['name']}: orders for this household are held until it is recorded",
                        "due_on": due.isoformat(), "state": "overdue", "source": kyc_india.CITATION, "assumed": None,
                        "days": (due - today).days}
                out.append({"item": item, "household": h, "owner": adviser, "scope": "household", "note": note})
    for row in out:
        row["exists"] = await _exists(session, firm.id, _title(row["item"]), row["household"].id if row["household"] else None)
    return {"applies": True, "as_of": today.isoformat(), "items": out}


async def view(session: AsyncSession, firm: Firm) -> dict:
    """The plan as rows a person reads: each near or overdue date, its owner and whether its task exists."""
    p = await plan(session, firm)
    if not p["applies"]:
        return {**p, "reminders": []}
    rows = [{"label": r["item"]["label"], "kind": r["item"]["kind"], "due_on": r["item"]["due_on"], "days": r["item"]["days"],
             "state": r["item"]["state"], "scope": r["scope"], "household": r["household"].name if r["household"] else None,
             "owner": r["owner"].full_name if r["owner"] else None, "task_exists": r["exists"], "source": r["item"]["source"],
             "assumed": r["item"].get("assumed")} for r in p["items"]]
    raising = sum(1 for r in rows if not r["task_exists"])
    return {"applies": True, "as_of": p["as_of"], "reminders": rows, "to_raise": raising, "tasks_exist": len(rows) - raising}


async def raise_due(session: AsyncSession, firm: Firm) -> dict:
    """Raise the task for each near or overdue item that has none. Idempotent: a second run raises nothing new."""
    p = await plan(session, firm)
    if not p["applies"]:
        return {"applies": False, "raised": 0, "already": 0, "tasks": []}
    raised, already = [], 0
    for row in p["items"]:
        if row["exists"]:
            already += 1
            continue
        t = _task(firm.id, row["item"], household=row["household"], owner=row["owner"], unassigned_note=row["note"])
        session.add(t)
        raised.append({"title": t.title, "due_on": row["item"]["due_on"], "household": row["household"].name if row["household"] else None,
                       "to": row["owner"].full_name if row["owner"] else None, "scope": row["scope"]})
    await session.flush()
    return {"applies": True, "as_of": p["as_of"], "raised": len(raised), "already": already, "tasks": raised,
            "note": "Tasks only: no filing, KYC or guardian record is changed, and nobody is told. This does not say the firm is compliant."}
