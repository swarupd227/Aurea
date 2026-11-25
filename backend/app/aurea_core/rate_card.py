"""The February rate card, a standing review (L100 India edition §1 and §8.2, B-300).

The L100 edition says to treat every Indian rate and threshold as "verify before client-facing use", that the Union Budget each February
can move tax rates and that SEBI's circulars through the year change fee caps, product rules and KYC, and that a current-year rate card is
a standing artefact an Indian advisory team keeps each February. `law_figures` holds each figure with its source and as-of day, and
`counsel_review` holds whether counsel has read it. This turns the calendar into a task.

**The list.** Every Indian law figure whose source is a Union Budget measure (the Finance Bill or Act and its Memorandum, the
Income-tax Act's rates, slabs, surcharge, cess, rebates and the Cost Inflation Index) or a SEBI circular (a master circular or a numbered
circular). A figure is placed by its source text and its key, in `kind_of`, so a new figure falls into the list by being cited. A figure from
a Regulation, another Act, a date or a rule is not on it, whatever else is true of it. Each row carries the figure's source and as-of day
and counsel's last review of it (or that there is none), and compliance may mark a Budget figure *awaiting the Budget*
(`counsel_review.record_watch`) until a new value is recorded.

**The task.** In February on the firm's calendar, once for that year and no more, one task is raised for compliance, listing the figures.
It is raised by the worker's scheduler (`atlas/worker.py`) every few hours; the check that nothing for the year exists is what makes it once.
It is a reminder: it changes no figure, and nothing here says a figure is out of date or right.
"""
from __future__ import annotations

import calendar
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import counsel_review, firm_time, law_figures
from app.core.security import UserRole
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.identity import User
from app.models.tenant import Firm
from app.provenance import ledger

SOURCE = "rate_card"
MONTH = 2
BUDGET_RE = re.compile(r"Budget|Finance Bill|Memorandum|tax reckoner", re.I)
BUDGET_KEYS = ("in_cg_", "in_new_regime_", "in_old_regime_", "in_surcharge_", "in_cess", "in_cii_", "in_loss_carry")
SEBI_RE = re.compile(r"SEBI[^.;]*?(?:circular|Circular)|Master Circular", re.I)
KINDS = {"budget": "Union Budget", "sebi_circular": "SEBI circular"}
NOT_A_FINDING = "A figure on this list is one to re-read against its source each February. Nothing here says a figure is out of date, or right."


def kind_of(fig: law_figures.LawFigure) -> str | None:
    """Whether the Budget or a SEBI circular sets this figure, by its key and its source; None for anything else."""
    if not fig.key.startswith("in_"):
        return None
    if fig.key.startswith(BUDGET_KEYS) or (BUDGET_RE.search(fig.source) and "Budget proposal" not in fig.source and not SEBI_RE.search(fig.source)):
        return "budget"
    if SEBI_RE.search(fig.source):
        return "sebi_circular"
    return None


def listed_keys(today: date) -> list[tuple[str, str]]:
    """(key, kind) of every Indian figure on the rate card, in key order."""
    out = []
    for key in sorted(k for k in law_figures.VERSIONS if k.startswith("in_")):
        kind = kind_of(law_figures.get(key, today))
        if kind:
            out.append((key, kind))
    return out


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not counsel_review.applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "figures": []}
    reviews = await counsel_review.standing(session, firm.id)
    watches = await counsel_review.standing_watch(session, firm.id)
    rows = []
    for key, kind in listed_keys(today):
        fig = law_figures.get(key, today)
        rows.append({**fig.view(today), "kind": kind, "kind_label": KINDS[kind], "counsel": counsel_review.status_of(key, reviews.get(key), today, watches.get(key))})
    count = lambda pred: sum(1 for r in rows if pred(r))  # noqa: E731
    year = review_year(today)
    task = await task_for(session, firm.id, year)
    return {"applies": True, "as_of": today.isoformat(), "year": year, "in_february": today.month == MONTH, "figures": rows,
            "counts": {"total": len(rows), "budget": count(lambda r: r["kind"] == "budget"), "sebi_circular": count(lambda r: r["kind"] == "sebi_circular"),
                       "not_reviewed": count(lambda r: r["counsel"]["state"] == "not_reviewed"),
                       "awaiting_budget": count(lambda r: r["counsel"]["state"] == "awaiting_budget")},
            "task": {"raised": task is not None, "status": str(task.status) if task is not None else None, "due_date": task.due_date.isoformat() if task and task.due_date else None},
            "next_task_on": next_task_day(today).isoformat(), "note": NOT_A_FINDING}


def review_year(today: date) -> int:
    """The year whose February this is, or the next to come."""
    return today.year if today.month <= MONTH else today.year + 1


def next_task_day(today: date) -> date:
    return date(review_year(today), MONTH, 1) if today.month != MONTH else today


def label(year: int) -> str:
    return f"Rate card {year}"


async def task_for(session: AsyncSession, firm_id: uuid.UUID, year: int) -> Task | None:
    return (await session.execute(select(Task).where(Task.firm_id == firm_id, Task.source == SOURCE, Task.subject_label == label(year))
                                  .order_by(Task.created_at).limit(1))).scalars().first()


async def raise_task(session: AsyncSession, firm: Firm, *, today: date | None = None) -> dict:
    """Raises the year's rate-card task once, in February, for compliance. A second call, any day, finds it and raises nothing."""
    day = today or firm_time.today(firm)
    if not counsel_review.applies(firm):
        return {"raised": False, "why": "not an Indian firm"}
    if day.month != MONTH:
        return {"raised": False, "why": "it is not February on the firm's calendar"}
    existing = await task_for(session, firm.id, day.year)
    if existing is not None:
        return {"raised": False, "why": f"the {day.year} rate-card task already exists", "task_id": str(existing.id)}
    view = await overview(session, firm)
    owner = (await session.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.COMPLIANCE, User.is_active.is_(True))
                                   .order_by(User.created_at).limit(1))).scalars().first()
    lines = []
    for r in view["figures"]:
        c = r["counsel"]
        last = f"{c['label']}" + (f", {c['reviewed_on']}" if c.get("reviewed_on") else "")
        lines.append(f"{r['key']} ({r['kind_label']}, as of {r['as_of']}): {last}")
    detail = (f"The Union Budget and SEBI's circulars can move these {view['counts']['total']} Indian figures; each is listed with its source's kind, "
              f"the day it was last read from its source and counsel's last review. Re-read each against its source and record counsel's review, or mark a "
              f"Budget figure awaiting the Budget until its new value is recorded. {NOT_A_FINDING}\n" + "\n".join(lines))
    task = Task(firm_id=firm.id, household_id=None, title=f"Review the {day.year} Indian rate card against the Union Budget and SEBI's circulars",
                detail=detail, status=TaskStatus.OPEN, due_date=date(day.year, MONTH, calendar.monthrange(day.year, MONTH)[1]), source=SOURCE,
                subject_label=label(day.year), assigned_to=owner.id if owner else None, assigned_by=None)
    session.add(task)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="rate_card_task_raised", actor="scheduler", content={
        "year": day.year, "figures": view["counts"]["total"], "task_id": str(task.id), "assigned_to": str(owner.id) if owner else None})
    return {"raised": True, "task_id": str(task.id), "year": day.year, "figures": view["counts"]["total"], "assigned": bool(owner)}
