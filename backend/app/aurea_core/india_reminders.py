"""A date that is near, or past, becomes a task for the person who owns it (India polish, B-247).

The compliance calendar and the countdown only showed dates. This reads the same items (`india_deadlines`, which reads
`compliance_calendar`, `governance` and the KYC records) and raises one task per item, once, for its owner:

  the firm's    a filing, the registration's expiry, a policy's review and an adviser's NISM certificate (expired or due within 90 days, once
                per person, certificate and expiry: B-298) go to compliance (the compliance officer the firm
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

import re
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
FIRM_KINDS = ("filing", "registration", "policy", "certification")
HOUSEHOLD_KINDS = ("kyc_review", "majority", "mandate", "lrs", "maturity")
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
        and item["days"] <= item.get("remind_within", WITHIN_DAYS)


async def _raised(session: AsyncSession, firm_id: uuid.UUID) -> set[tuple[str, uuid.UUID | None]]:
    """The title and household of every task this job has raised, whatever its state, in one question."""
    return {(t, h) for t, h in (await session.execute(select(Task.title, Task.household_id).where(Task.firm_id == firm_id, Task.source == SOURCE))).all()}


def _title(item: dict) -> str:
    return f"India: {item['label']}"[:tasks.TITLE_MAX]


def _sentence(text) -> str:
    """The text ended with one full stop, whether or not it came with one."""
    return f"{str(text).rstrip(' .')}."


def _detail(item: dict) -> str:
    """The due day and the source, with no "due today" or "overdue by": a stored sentence is read on later days, and the task's own
    due date says where it stands."""
    out = f"Due {item['due_on']}. Source: {_sentence(item['source'])}"
    if item.get("assumed"):
        out += f" Assumed: {_sentence(item['assumed'])}"
    return out


# What a task raised before B-372 carried: the day's relative wording, and a source that ended in a full stop given another.
_RELATIVE = re.compile(r"^(Due \d{4}-\d{2}-\d{2}) \((?:overdue by \d+ day\(s\)|due today|due in \d+ day\(s\))\)\.")
_TWO_STOPS = re.compile(r"(?<!\.)\.\.(?!\.)")


def corrected_detail(detail: str | None) -> str | None:
    """A stored description in the present form. Pure, and the same however often it runs; text not in this job's form is left."""
    if not detail or not detail.startswith("Due "):
        return detail
    return _TWO_STOPS.sub(".", _RELATIVE.sub(r"\1.", detail))


async def correct_open_details(session: AsyncSession, firm_id: uuid.UUID) -> int:
    """Rewrite the description of each OPEN task this job raised into the present form. It raises, reopens, closes and deletes nothing."""
    rows = (await session.execute(select(Task).where(Task.firm_id == firm_id, Task.source == SOURCE, Task.status == TaskStatus.OPEN))).scalars().all()
    changed = 0
    for t in rows:
        new = corrected_detail(t.detail)
        if new != t.detail:
            t.detail = new
            changed += 1
    await session.flush()
    return changed


def _task(firm_id: uuid.UUID, item: dict, *, household: Household | None, owner: User | None, unassigned_note: str | None = None) -> Task:
    detail = _detail(item) + (f" {unassigned_note}" if owner is None and unassigned_note else "")
    return Task(firm_id=firm_id, household_id=household.id if household else None, title=_title(item), detail=detail, status=TaskStatus.OPEN,
                due_date=date.fromisoformat(item["due_on"]), source=SOURCE, subject_label=household.name if household else "The firm",
                assigned_to=owner.id if owner else None, assigned_by=None)


MATURITY_WITHIN_DAYS = 30
LRS_NEAR = 0.90          # a person who has remitted this share of the year's limit gets a reminder (B-310)


def _item(kind: str, label: str, due: date, today: date, source: str, assumed: str | None = None) -> dict:
    days = (due - today).days
    return {"kind": kind, "label": label, "due_on": due.isoformat(), "state": "overdue" if days < 0 else "due_soon", "source": source, "assumed": assumed, "days": days}


async def _batch_items(session: AsyncSession, firm: Firm, household_id: uuid.UUID, today: date) -> list[dict]:
    """A household's other dates (B-310, B-311): a SIP debit mandate that ends within 30 days, a person who has remitted nine tenths of the year's
    overseas limit, and a small-savings or bond holding that matures, or a lock-in that ends, within 30 days. Each reads what a person recorded:
    a holding with no maturity day is not raised, and none of it moves money or registers anything."""
    from app.aurea_core import india_savings, lrs, sip_mandates

    out: list[dict] = []
    for m in (await sip_mandates.overview(session, firm, household_id=household_id)).get("mandates", []):
        if m["state"] == "active" and m.get("expires_on"):
            due = date.fromisoformat(m["expires_on"])
            if (due - today).days <= MATURITY_WITHIN_DAYS:
                out.append(_item("mandate", f"SIP mandate {m['reference']} ends on {m['expires_on']}: a debit after it fails until it is renewed", due, today,
                                 "The debit mandate's own end day, as a person recorded it"))
    for p in (await lrs.household_view(session, firm, household_id)).get("people", []):
        if p.get("state") == "assessed" and p["limit_usd"] and p["remitted_usd"] >= LRS_NEAR * p["limit_usd"]:
            out.append(_item("lrs", f"Overseas remittance limit nearly reached for {p['person']}, {p['year']}: a further remittance may go over it", today, today,
                             "RBI's Liberalised Remittance Scheme limit as recorded in law_figures; the remittances are those a person recorded"))
    for h in (await india_savings.household_view(session, firm, household_id)).get("holdings", []):
        for key, verb in (("maturity_on", "matures"), ("locked_in_to", "is out of its lock-in")):
            day = h.get(key)
            if day and (date.fromisoformat(day) - today).days <= MATURITY_WITHIN_DAYS and (today - date.fromisoformat(day)).days <= MATURITY_WITHIN_DAYS:
                out.append(_item("maturity", f"{h['label']} at {h['institution']} {verb} on {day}", date.fromisoformat(day), today,
                                 "The statement's own maturity or lock-in day, as a person recorded it"))
    return out


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
        for i in (await india_deadlines.household_items(session, firm, h.id, nominations=False))["items"]:
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
        for item in await _batch_items(session, firm, h.id, today):
            out.append({"item": item, "household": h, "owner": adviser, "scope": "household", "note": note})
    raised = await _raised(session, firm.id)
    for row in out:
        row["exists"] = (_title(row["item"]), row["household"].id if row["household"] else None) in raised
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
