"""What gives a household's plan a reason to be looked at again, and how fast the plan was (B-188, L200-2 §7 and §10).

A trigger is something recorded on the file that bears on the plan: a life event (a marriage, an inheritance, a business
sale, a job change and the rest, client_file.LIFE_EVENTS) or an equity grant (equity_comp). A life event has raised a review
task since it was built; a grant now does too (`raise_for_grant`), for the client's recorded adviser when there is one.

The plan is "touched" by the next event on its record (plan_record: a baseline, a review or a delivery) recorded at or after
the trigger was. The clock runs from the day the trigger was recorded, which is the day the firm learned of it, not the
day it happened, and stops at that event's day, both on the firm's calendar. An open trigger shows how long it has waited.
No target is supplied: a firm that wants one states it. Nothing here changes a plan, a goal or a task.

A household also has a tier, which says how much of the plan a trigger asks for: a full rebuild, or a review and refresh.
A person sets it with a reason, from what they know of the household; it is added and never edited, the latest stands, and
nothing sets it from the household's size. With none set it reads "not set".
"""
from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, plan_record
from app.models.client_file import LifeEvent
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.equity_comp import EquityGrant
from app.models.graph import Household, Person
from app.models.plan_record import PlanRecordEvent
from app.models.plan_tier import PLAN_TIERS, PlanTier
from app.models.tenant import Firm
from app.provenance import ledger

TASK_SOURCE = "plan_trigger"
TASK_DAYS = 30
GRANT_TOUCHES = ["plan", "concentration", "tax", "equity_compensation"]
TIER_WORDS = {"full_rebuild": "full rebuild", "review_and_refresh": "review and refresh"}
DECIDES = "nothing: a trigger asks for a look at the plan, and a person decides what to do"


class PlanTriggerError(ValueError):
    """This cannot be recorded, with the reason."""


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _day(moment: datetime, firm: Firm | None) -> date:
    return firm_time.today(firm, _utc(moment))


async def raise_for_grant(session: AsyncSession, firm_id: uuid.UUID, household: Household, grant: EquityGrant) -> Task:
    """The review task a recorded equity grant raises, as a life event raises one. For the client's recorded adviser when
    there is one, else unassigned. It changes no figure."""
    firm = await session.get(Firm, firm_id)
    adviser = await advisers.adviser_of(session, firm_id, household.id)
    adviser = adviser if adviser is not None and adviser.is_active else None
    task = Task(firm_id=firm_id, household_id=household.id, title=f"Review after equity grant: {household.name}"[:280],
                detail=f"Look at: {', '.join(t.replace('_', ' ') for t in GRANT_TOUCHES)}.", status=TaskStatus.OPEN,
                due_date=firm_time.today(firm) + timedelta(days=TASK_DAYS), source=TASK_SOURCE, subject_label=household.name,
                assigned_to=adviser.id if adviser else None)
    session.add(task)
    await session.flush()
    return task


def _tier_view(t: PlanTier | None) -> dict | None:
    if t is None:
        return None
    return {"tier": t.tier, "label": TIER_WORDS.get(t.tier, t.tier), "reason": t.reason, "as_of": t.as_of.isoformat(), "set_by": t.set_by}


async def _tiers(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PlanTier]]:
    out: dict[uuid.UUID, list[PlanTier]] = {h: [] for h in household_ids}
    if not household_ids:
        return out
    for t in (await session.execute(select(PlanTier).where(PlanTier.firm_id == firm_id, PlanTier.household_id.in_(household_ids))
                                    .order_by(PlanTier.created_at, PlanTier.as_of))).scalars().all():
        out[t.household_id].append(t)
    return out


async def _collect(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID]) -> tuple[dict, dict, dict]:
    """Each household's triggers and plan-record events, and the people's names."""
    life: dict = {h: [] for h in household_ids}
    grants: dict = {h: [] for h in household_ids}
    events: dict = {h: [] for h in household_ids}
    if not household_ids:
        return life, grants, events
    for e in (await session.execute(select(LifeEvent).where(LifeEvent.firm_id == firm_id, LifeEvent.household_id.in_(household_ids)))).scalars().all():
        life[e.household_id].append(e)
    for g in (await session.execute(select(EquityGrant).where(EquityGrant.firm_id == firm_id, EquityGrant.household_id.in_(household_ids)))).scalars().all():
        grants[g.household_id].append(g)
    for p in (await session.execute(select(PlanRecordEvent).where(PlanRecordEvent.firm_id == firm_id, PlanRecordEvent.household_id.in_(household_ids))
                                    .order_by(PlanRecordEvent.created_at, PlanRecordEvent.seq))).scalars().all():
        events[p.household_id].append(p)
    return life, grants, events


def _triggers(life: list[LifeEvent], grants: list[EquityGrant], events: list[PlanRecordEvent], names: dict, firm: Firm | None, today: date) -> list[dict]:
    rows = []
    for e in life:
        rows.append({"id": str(e.id), "kind": "business_sale" if e.kind == "business_sale" else "life_event", "what": e.kind,
                     "person": names.get(e.person_id), "occurred_on": e.happened_on.isoformat(), "_at": _utc(e.created_at), "document": e.source_document})
    for g in grants:
        rows.append({"id": str(g.id), "kind": "equity_grant", "what": g.kind, "person": names.get(g.person_id), "occurred_on": g.grant_date.isoformat(),
                     "_at": _utc(g.created_at), "document": g.source_document})
    rows.sort(key=lambda r: r["_at"])
    out = []
    for r in rows:
        recorded = _day(r["_at"], firm)
        touch = next((p for p in events if _utc(p.created_at) >= r["_at"]), None)
        days = max((touch.happened_on - recorded).days, 0) if touch else None
        out.append({**{k: v for k, v in r.items() if k != "_at"}, "recorded_on": recorded.isoformat(),
                    "answered_on": touch.happened_on.isoformat() if touch else None, "answered_by": touch.kind if touch else None,
                    "days_to_answer": days, "open_days": None if touch else max((today - recorded).days, 0)})
    return out


def _summary(triggers: list[dict]) -> dict:
    answered = [t["days_to_answer"] for t in triggers if t["answered_on"]]
    open_ = [t["open_days"] for t in triggers if not t["answered_on"]]
    return {"triggers": len(triggers), "answered": len(answered), "open": len(open_),
            "median_days": statistics.median(answered) if answered else None, "slowest_days": max(answered) if answered else None,
            "longest_open_days": max(open_) if open_ else None}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    life, grants, events = await _collect(session, firm_id, [household.id])
    names = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.household_id == household.id))).scalars().all()}
    triggers = _triggers(life[household.id], grants[household.id], events[household.id], names, firm, today)
    tier = (await _tiers(session, firm_id, [household.id]))[household.id]
    return {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "tier": _tier_view(tier[-1] if tier else None),
            "tier_history": [_tier_view(t) for t in reversed(tier)], "tiers": [{"key": k, "label": v} for k, v in TIER_WORDS.items()],
            "triggers": list(reversed(triggers)), "summary": _summary(triggers), "plan_touches": [e.kind for e in events[household.id]],
            "target": "none: no target is supplied, and a firm that wants one states it", "decides": DECIDES}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household with a trigger, how long its oldest open one has waited, and the firm's median time to answer."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    households = (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None)).order_by(Household.name))).scalars().all()
    ids = [h.id for h in households]
    life, grants, events = await _collect(session, firm_id, ids)
    tiers = await _tiers(session, firm_id, ids)
    rows, every = [], []
    for h in households:
        names = {}
        ts = _triggers(life[h.id], grants[h.id], events[h.id], names, firm, today)
        if not ts:
            continue
        every += ts
        s = _summary(ts)
        t = tiers[h.id][-1] if tiers[h.id] else None
        rows.append({"household_id": str(h.id), "household": h.name, "tier": _tier_view(t), **s})
    rows.sort(key=lambda r: (-(r["longest_open_days"] or -1), r["household"]))
    return {"as_of": today.isoformat(), "households": rows, "summary": {**_summary(every), "households_with_triggers": len(rows),
                                                                      "tier_not_set": sum(1 for r in rows if r["tier"] is None)},
            "target": "none: no target is supplied, and a firm that wants one states it", "decides": DECIDES}


def _may_set(role: Any) -> None:
    if role is None or str(role) not in plan_record.PREPARERS:
        raise PlanTriggerError("A household's plan tier is set by an adviser or a paraplanner.")


async def prepare_tier(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, household_id: Any, tier: Any, reason: Any) -> dict:
    """What setting a tier would refuse, refused without writing."""
    _may_set(role)
    try:
        hid = uuid.UUID(str(household_id))
    except (TypeError, ValueError):
        raise PlanTriggerError("household_id must be an id from a read tool.")
    household = await session.get(Household, hid)
    if household is None or household.firm_id != firm_id:
        raise PlanTriggerError("That household is not in this firm.")
    if tier not in PLAN_TIERS:
        raise PlanTriggerError(f"The tier must be one of {', '.join(PLAN_TIERS)}.")
    why = str(reason or "").strip()
    if len(why) < 5:
        raise PlanTriggerError("Say why this household's plan is that tier: what you know of it that sets how much a trigger asks for.")
    if len(why) > 500:
        raise PlanTriggerError("A reason is at most 500 characters.")
    was = (await _tiers(session, firm_id, [hid]))[hid]
    now = was[-1] if was else None
    if now is not None and now.tier == tier:
        raise PlanTriggerError(f"This household's plan is already a {TIER_WORDS[tier]}.")
    return {"household": household, "tier": tier, "label": TIER_WORDS[tier], "reason": why, "was": now}


async def set_tier(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, **terms) -> PlanTier:
    ready = await prepare_tier(session, firm_id, role=role, **terms)
    firm = await session.get(Firm, firm_id)
    row = PlanTier(firm_id=firm_id, household_id=ready["household"].id, tier=ready["tier"], reason=ready["reason"], as_of=firm_time.today(firm), set_by=recorded_by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="plan_tier_set", actor=recorded_by, content={
        "household_id": str(ready["household"].id), "tier": row.tier, "replaced": ready["was"].tier if ready["was"] else None, "reason": row.reason})
    return row
