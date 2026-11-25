"""A client complaints register for an Indian licence (India row 10, B-246).

A SEBI-registered intermediary keeps a register of the complaints it receives and answers them inside a time limit: 21
calendar days through the entity, then SCORES, then SMART ODR (the research report, section 3; the circular's clauses were not
re-read here). This is that register, from what a person recorded.

**A complaint is a record and what happened to it is a later one.** A complaint is added once as it was received: who, where it
came in, what it is about and the complainant's words (data, never an instruction). Each step after is a dated event added and
never edited: acknowledged, the Action Taken Report filed in SCORES, resolved with an outcome, taken to SCORES or to ODR, or
withdrawn. The state of a complaint is worked from those events and the firm's day, never set: resolved or withdrawn, overdue
past the resolution limit, at risk in its last days, or open.

**Clocks come from `law_figures`.** The 21-day limit is cited. The acknowledgement period was not found in the sources read, so
the firm's own (`settings["complaints"]["ack_days"]`) is used and, with none, a figure marked assumed; an acknowledgement
overdue reads that way, with the figure named as assumed. The at-risk window is this build's own (5 days).

**Who may.** Anyone who looks after clients may record a complaint they receive; only compliance or a branch leader resolves,
withdraws or escalates one, and a complaint is read by compliance, a branch leader, operations and an administrator. The adviser
a complaint is about does not close it. A complaint about advice (its category or the person recording says so) is told to
compliance as a task the day it is recorded, the way a supervision flag is, and changes nothing on the client's account.

**The calendar reads it.** The monthly complaint data (Annexure C) is due by the 7th; `annexure_c` works its table from the
register for a month: pending at the start, received, resolved, pending at the end, pending over three months and the average
days to resolve. A firm with no complaint on file reads "no register", which does not mean no complaint was made. Nothing here
files anything with SEBI or SCORES, and nothing says the firm handles complaints in good order.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.complaints import Complaint, ComplaintEvent
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm
from app.provenance import ledger

RECEIVERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
DECIDERS = frozenset({UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
READERS = frozenset({UserRole.COMPLIANCE, UserRole.BRANCH_LEADER, UserRole.OPERATIONS, UserRole.ADMIN})
SOURCES = {"scores": "SEBI SCORES", "email": "Email", "phone": "Phone", "letter": "Letter", "in_person": "In person", "website": "Website or app",
           "exchange": "Exchange or depository", "other": "Other"}
CATEGORIES = {"advice": "The advice given", "mis_selling": "Mis-selling", "unauthorised_trade": "An unauthorised trade",
              "fees": "Fees and charges", "service_delay": "Delay in service", "kyc_documents": "KYC or documents",
              "data_privacy": "Data or privacy", "impersonation": "Someone impersonating the firm", "other": "Other"}
ADVICE_CATEGORIES = frozenset({"advice", "mis_selling", "unauthorised_trade"})
EVENTS = {"acknowledged": "Acknowledged", "atr_filed": "Action Taken Report filed in SCORES", "resolved": "Resolved",
          "escalated_scores": "Taken to SCORES by the complainant", "escalated_odr": "Taken to SMART ODR", "withdrawn": "Withdrawn"}
CLOSING = ("resolved", "withdrawn")
OUTCOMES = {"upheld": "Upheld", "partly_upheld": "Partly upheld", "not_upheld": "Not upheld"}
AT_RISK_DAYS = 5     # this build's own: how near the limit a complaint is called at risk
CITATION = ("SEBI circular of Sep 2023 on redressal of investor grievances through SCORES, linked to online dispute resolution, "
            "and the Master Circular for Investment Advisers para 7.5, as read in the research report; clauses not re-read")
# Annexure C groups, as this build reads the table: where the complaint came from.
GROUPS = (("scores", "SEBI SCORES", ("scores",)), ("exchange", "Exchanges and depositories", ("exchange",)),
          ("direct", "Directly from clients", ("email", "phone", "letter", "in_person", "website", "other")))


class ComplaintError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def clocks(firm: Firm, today: date) -> dict:
    resolve = int(law_figures.value("in_complaint_resolve_days", today))
    own = ((firm.settings or {}).get("complaints") or {}).get("ack_days")
    try:
        own = int(own) if own is not None else None
    except (TypeError, ValueError):
        own = None
    ack = own if own and own > 0 else int(law_figures.value("in_complaint_ack_days", today))
    return {"resolve_days": resolve, "ack_days": ack, "ack_assumed": not (own and own > 0), "at_risk_days": AT_RISK_DAYS,
            "citations": law_figures.cited(["in_complaint_resolve_days", "in_complaint_ack_days"], today)}


def _uuid(value, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ComplaintError(f"Give the {label}.")


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ComplaintError(f"Give the {label} as YYYY-MM-DD.")


def worked(c: Complaint, events: list[ComplaintEvent], clock: dict, today: date) -> dict:
    """The state of one complaint from its events and the day."""
    by_kind: dict[str, ComplaintEvent] = {}
    for e in sorted(events, key=lambda e: (e.happened_on, e.created_at)):
        by_kind.setdefault(e.kind, e)
    closing = next((by_kind[k] for k in CLOSING if k in by_kind), None)
    due = c.received_on + timedelta(days=clock["resolve_days"])
    ack_due = c.received_on + timedelta(days=clock["ack_days"])
    ack = by_kind.get("acknowledged")
    if closing is not None:
        status = closing.kind
        days_taken = (closing.happened_on - c.received_on).days
        late = closing.kind == "resolved" and closing.happened_on > due
    else:
        days_taken, late = None, False
        left = (due - today).days
        status = "overdue" if left < 0 else "at_risk" if left <= clock["at_risk_days"] else "open"
    return {"status": status, "due_on": due.isoformat(), "days_left": None if closing else (due - today).days,
            "ack_due_on": ack_due.isoformat(), "acknowledged_on": ack.happened_on.isoformat() if ack else None,
            "ack_state": "acknowledged" if ack else ("ack_overdue" if today > ack_due and closing is None else "ack_due"),
            "ack_late": bool(ack and ack.happened_on > ack_due),
            "closed_on": closing.happened_on.isoformat() if closing else None, "outcome": by_kind["resolved"].outcome if "resolved" in by_kind else None,
            "days_taken": days_taken, "resolved_late": late,
            "atr_filed": "atr_filed" in by_kind, "escalated_scores": "escalated_scores" in by_kind, "escalated_odr": "escalated_odr" in by_kind,
            "long_pending": closing is None and c.received_on <= _months_back(today, int(law_figures.value("in_complaint_pending_months", today)))}


def _months_back(day: date, months: int) -> date:
    y, m = day.year, day.month - months
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


async def _events(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, list[ComplaintEvent]]:
    out: dict[uuid.UUID, list[ComplaintEvent]] = {}
    for e in (await session.execute(select(ComplaintEvent).where(ComplaintEvent.firm_id == firm_id))).scalars().all():
        out.setdefault(e.complaint_id, []).append(e)
    return out


def view(c: Complaint, events: list[ComplaintEvent], clock: dict, today: date, household: str | None = None) -> dict:
    w = worked(c, events, clock, today)
    return {"id": str(c.id), "reference": c.reference, "scores_reference": c.scores_reference, "complainant": c.complainant,
            "household": household, "household_id": str(c.household_id) if c.household_id else None, "source": c.source,
            "source_label": SOURCES.get(c.source, c.source), "category": c.category, "category_label": CATEGORIES.get(c.category, c.category),
            "about_advice": bool(c.about_advice), "summary": c.summary, "received_on": c.received_on.isoformat(),
            "recorded_by": c.recorded_by, "document": c.document, "told_compliance": c.task_id is not None, **w,
            "events": [{"kind": e.kind, "label": EVENTS.get(e.kind, e.kind), "on": e.happened_on.isoformat(), "outcome": e.outcome,
                        "reference": e.reference, "note": e.note, "by": e.recorded_by}
                       for e in sorted(events, key=lambda e: (e.happened_on, e.created_at))]}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "complaints": [], "counts": {}}
    clock = clocks(firm, today)
    rows = (await session.execute(select(Complaint).where(Complaint.firm_id == firm.id).order_by(Complaint.received_on.desc()))).scalars().all()
    events = await _events(session, firm.id)
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    items = [view(c, events.get(c.id, []), clock, today, names.get(c.household_id)) for c in rows]
    order = {"overdue": 0, "at_risk": 1, "open": 2, "resolved": 3, "withdrawn": 4}
    items.sort(key=lambda i: (order[i["status"]], i["due_on"] if i["status"] in ("overdue", "at_risk", "open") else ""))
    counts = {s: sum(1 for i in items if i["status"] == s) for s in order}
    counts["total"] = len(items)
    counts["about_advice_open"] = sum(1 for i in items if i["about_advice"] and i["status"] in ("overdue", "at_risk", "open"))
    counts["ack_overdue"] = sum(1 for i in items if i["ack_state"] == "ack_overdue")
    counts["long_pending"] = sum(1 for i in items if i["long_pending"])
    counts["escalated"] = sum(1 for i in items if i["escalated_scores"] or i["escalated_odr"])
    prev = today.replace(day=1) - timedelta(days=1)
    return {"applies": True, "as_of": today.isoformat(), "complaints": items, "counts": counts, "clocks": clock, "citation": CITATION,
            "annexure_c": await annexure_c(session, firm, f"{prev.year}-{prev.month:02d}") if rows else None,
            "sources": SOURCES, "categories": CATEGORIES, "events": EVENTS, "outcomes": OUTCOMES,
            "not_compliance": "A register of what was recorded and the limits it runs against; it does not say the firm handles complaints in good order."}


async def annexure_c(session: AsyncSession, firm: Firm, period: str) -> dict | None:
    """The monthly complaint table for one month from the register, or None when no complaint was ever recorded."""
    if not (await session.execute(select(func.count()).select_from(Complaint).where(Complaint.firm_id == firm.id))).scalar_one():
        return None
    try:
        y, m = (int(x) for x in period.split("-"))
        start, end = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    except (ValueError, TypeError):
        raise ComplaintError("A period is YYYY-MM.")
    today = firm_time.today(firm)
    clock = clocks(firm, today)
    rows = (await session.execute(select(Complaint).where(Complaint.firm_id == firm.id))).scalars().all()
    events = await _events(session, firm.id)
    long_months = int(law_figures.value("in_complaint_pending_months", today))
    table = []
    for key, label, sources in GROUPS:
        mine = [c for c in rows if c.source in sources]
        def closed_on(c):
            closing = [e.happened_on for e in events.get(c.id, []) if e.kind in CLOSING]
            return min(closing) if closing else None
        start_pending = [c for c in mine if c.received_on < start and (closed_on(c) is None or closed_on(c) >= start)]
        received = [c for c in mine if start <= c.received_on <= end]
        resolved = [c for c in mine if closed_on(c) is not None and start <= closed_on(c) <= end]
        end_pending = [c for c in mine if c.received_on <= end and (closed_on(c) is None or closed_on(c) > end)]
        long = [c for c in end_pending if c.received_on <= _months_back(end, long_months)]
        days = [(closed_on(c) - c.received_on).days for c in resolved]
        table.append({"group": key, "label": label, "pending_at_start": len(start_pending), "received": len(received),
                      "resolved": len(resolved), "pending_at_end": len(end_pending), "pending_over_months": len(long),
                      "average_days": round(sum(days) / len(days), 1) if days else None})
    total = {k: sum(r[k] for r in table) for k in ("pending_at_start", "received", "resolved", "pending_at_end", "pending_over_months")}
    allday = [d for r in table for d in ([r["average_days"]] if r["average_days"] is not None else [])]
    return {"period": period, "rows": table, "total": total, "pending_months": long_months, "average_days": round(sum(allday) / len(allday), 1) if allday else None,
            "resolve_days": clock["resolve_days"],
            "note": "Worked from the register as recorded, in the table's groups as this build reads them; the circular's table was not re-read."}


async def period_counts(session: AsyncSession, firm: Firm, period: str) -> dict | None:
    """The calendar's monthly complaint filing reads this: what the register shows for the month, or None where there is no register."""
    t = await annexure_c(session, firm, period)
    if t is None:
        return None
    return {"received": t["total"]["received"], "resolved": t["total"]["resolved"], "pending_at_end": t["total"]["pending_at_end"],
            "pending_over_months": t["total"]["pending_over_months"], "from_register": True}


# -- Recording -----------------------------------------------------------------------------------------------

async def find(session: AsyncSession, firm_id: uuid.UUID, ref) -> Complaint:
    text = str(ref or "").strip()
    c = None
    try:
        c = await session.get(Complaint, uuid.UUID(text))
    except ValueError:
        c = (await session.execute(select(Complaint).where(Complaint.firm_id == firm_id, Complaint.reference == text))).scalars().first()
    if c is None or c.firm_id != firm_id:
        raise ComplaintError("That complaint is not on the register here. read_complaints lists them by reference.")
    return c


async def _next_reference(session: AsyncSession, firm: Firm, today: date) -> str:
    n = (await session.execute(select(func.count()).select_from(Complaint).where(Complaint.firm_id == firm.id))).scalar_one() + 1
    while True:
        ref = f"CMP-{today.year}-{n:04d}"
        if not (await session.execute(select(Complaint.id).where(Complaint.firm_id == firm.id, Complaint.reference == ref))).first():
            return ref
        n += 1


async def prepare_complaint(session: AsyncSession, firm: Firm, role: UserRole, *, complainant: str | None, source: str | None, category: str | None,
                            summary: str | None, received_on=None, household_id=None, scores_reference: str | None = None,
                            reference: str | None = None, document: str | None = None, about_advice: bool | None = None) -> dict:
    if not applies(firm):
        raise ComplaintError("A complaints register is kept for an Indian firm.")
    if UserRole(role) not in RECEIVERS:
        raise ComplaintError("A complaint is recorded by someone who looks after clients: an adviser, paraplanner, operations, compliance or a branch leader.")
    today = firm_time.today(firm)
    who = " ".join((complainant or "").split())
    if len(who) < 2:
        raise ComplaintError("Name the complainant.")
    src = (source or "").strip().lower()
    if src not in SOURCES:
        raise ComplaintError(f"Say where it came in: one of {', '.join(SOURCES)}.")
    cat = (category or "").strip().lower()
    if cat not in CATEGORIES:
        raise ComplaintError(f"The category is one of {', '.join(CATEGORIES)}.")
    words = " ".join((summary or "").split())
    if len(words) < 10:
        raise ComplaintError("Give what the complainant said, in their words or a faithful summary.")
    on = _day(received_on, "date it was received") if received_on else today
    if on > today:
        raise ComplaintError("A complaint cannot be received in the future.")
    scores = (scores_reference or "").strip() or None
    if src == "scores" and not scores:
        raise ComplaintError("A complaint that came through SCORES carries its SCORES registration number.")
    household = None
    if household_id:
        household = await session.get(Household, _uuid(household_id, "household id"))
        if household is None or household.firm_id != firm.id:
            raise ComplaintError("That household is not this firm's.")
    ref = (reference or "").strip() or await _next_reference(session, firm, today)
    if (await session.execute(select(Complaint.id).where(Complaint.firm_id == firm.id, Complaint.reference == ref))).first():
        raise ComplaintError(f"Reference {ref} is already on the register.")
    advice = bool(about_advice) or cat in ADVICE_CATEGORIES
    return {"complainant": who[:200], "source": src, "category": cat, "summary": words[:4000], "received_on": on, "household": household,
            "scores_reference": scores[:60] if scores else None, "reference": ref[:40], "document": (document or "").strip()[:300] or None,
            "about_advice": advice, "clock": clocks(firm, today), "due_on": on + timedelta(days=clocks(firm, today)["resolve_days"])}


async def _compliance_user(session: AsyncSession, firm_id: uuid.UUID) -> User | None:
    return (await session.execute(select(User).where(User.firm_id == firm_id, User.role == UserRole.COMPLIANCE, User.is_active.is_(True))
                                  .order_by(User.full_name))).scalars().first()


async def record_complaint(session: AsyncSession, firm: Firm, role: UserRole, *, by: str, **terms) -> dict:
    p = await prepare_complaint(session, firm, role, **terms)
    row = Complaint(firm_id=firm.id, reference=p["reference"], scores_reference=p["scores_reference"],
                    household_id=p["household"].id if p["household"] else None, complainant=p["complainant"], source=p["source"],
                    category=p["category"], about_advice=p["about_advice"], summary=p["summary"], received_on=p["received_on"],
                    recorded_by=by, document=p["document"], created_at=utcnow())
    session.add(row)
    await session.flush()
    if p["about_advice"]:
        # A complaint about advice is compliance's to hear, and the adviser it is about does not close it.
        compliance = await _compliance_user(session, firm.id)
        task = Task(firm_id=firm.id, household_id=row.household_id, title=f"Complaint about advice, {row.reference}"[:280],
                    detail=f"Received {row.received_on.isoformat()} by {SOURCES[row.source].lower()}; resolution due {p['due_on'].isoformat()}. "
                           f"Open it on the complaints register.", status=TaskStatus.OPEN, due_date=p["due_on"], source="complaint",
                    subject_label=p["household"].name if p["household"] else row.complainant,
                    assigned_to=compliance.id if compliance else None, assigned_by=None)
        session.add(task)
        await session.flush()
        row.task_id = task.id
    await ledger.append_entry(session, firm_id=firm.id, event_type="complaint_recorded", actor=by, content={
        "complaint_id": str(row.id), "reference": row.reference, "source": row.source, "category": row.category,
        "about_advice": row.about_advice, "received_on": row.received_on.isoformat(), "scores_reference": row.scores_reference})
    return {"recorded": row.reference, **await overview(session, firm)}


async def prepare_event(session: AsyncSession, firm: Firm, role: UserRole, *, complaint, kind: str | None, happened_on=None,
                        outcome: str | None = None, reference: str | None = None, note: str | None = None) -> dict:
    if not applies(firm):
        raise ComplaintError("A complaints register is kept for an Indian firm.")
    c = await find(session, firm.id, complaint)
    k = (kind or "").strip().lower()
    if k not in EVENTS:
        raise ComplaintError(f"A step is one of {', '.join(EVENTS)}.")
    role = UserRole(role)
    if k in ("acknowledged", "atr_filed"):
        if role not in RECEIVERS:
            raise ComplaintError(f"{EVENTS[k]} is recorded by someone who looks after clients.")
    elif role not in DECIDERS:
        raise ComplaintError(f"{EVENTS[k]} is compliance's or a branch leader's to record: it is not decided by the adviser a complaint may be about.")
    today = firm_time.today(firm)
    on = _day(happened_on, "date") if happened_on else today
    if on > today:
        raise ComplaintError("A step cannot be dated in the future.")
    if on < c.received_on:
        raise ComplaintError(f"{c.reference} was received on {c.received_on.isoformat()}; a step cannot come before it.")
    events = (await session.execute(select(ComplaintEvent).where(ComplaintEvent.complaint_id == c.id))).scalars().all()
    have = {e.kind for e in events}
    if k in have and k not in ("atr_filed",):
        raise ComplaintError(f"{c.reference} already has '{EVENTS[k].lower()}' recorded.")
    closed = [e for e in events if e.kind in CLOSING]
    if closed and k not in ("escalated_scores", "escalated_odr"):
        raise ComplaintError(f"{c.reference} was {closed[0].kind} on {closed[0].happened_on.isoformat()}.")
    ref = (reference or "").strip()[:120] or None
    words = " ".join((note or "").split())[:2000] or None
    if k == "resolved":
        if (outcome or "").strip().lower() not in OUTCOMES:
            raise ComplaintError(f"A resolution names its outcome: {', '.join(OUTCOMES)}.")
        if not words or len(words) < 3:
            raise ComplaintError("Say what was done to resolve it, in a sentence the complainant could be sent.")
    if k == "withdrawn" and (not words or len(words) < 3):
        raise ComplaintError("Say how it was withdrawn: the complainant's own words or message.")
    if k == "atr_filed":
        if not c.scores_reference:
            raise ComplaintError(f"{c.reference} has no SCORES registration number, so there is no Action Taken Report to file.")
        if not ref:
            raise ComplaintError("Give the SCORES acknowledgement or reference for the report.")
    if k in ("escalated_scores", "escalated_odr") and not ref:
        raise ComplaintError("Give the SCORES or ODR registration number the complainant was given.")
    return {"complaint": c, "kind": k, "happened_on": on, "outcome": (outcome or "").strip().lower() or None if k == "resolved" else None,
            "reference": ref, "note": words, "late": k == "resolved" and on > c.received_on + timedelta(days=clocks(firm, today)["resolve_days"])}


async def record_event(session: AsyncSession, firm: Firm, role: UserRole, *, by: str, **terms) -> dict:
    p = await prepare_event(session, firm, role, **terms)
    c = p["complaint"]
    if p["kind"] in CLOSING:     # exactly once: lock and re-read
        await session.execute(select(Complaint).where(Complaint.id == c.id).with_for_update().execution_options(populate_existing=True))
        if (await session.execute(select(ComplaintEvent.id).where(ComplaintEvent.complaint_id == c.id, ComplaintEvent.kind.in_(CLOSING)))).first():
            raise ComplaintError(f"{c.reference} is already closed.")
    session.add(ComplaintEvent(firm_id=firm.id, complaint_id=c.id, kind=p["kind"], happened_on=p["happened_on"], outcome=p["outcome"],
                               reference=p["reference"], note=p["note"], recorded_by=by, created_at=utcnow()))
    await session.flush()
    if p["kind"] in CLOSING and c.task_id:
        t = await session.get(Task, c.task_id)
        if t is not None and str(t.status) == TaskStatus.OPEN.value:
            t.status = TaskStatus.DONE
    await ledger.append_entry(session, firm_id=firm.id, event_type="complaint_event_recorded", actor=by, content={
        "complaint_id": str(c.id), "reference": c.reference, "kind": p["kind"], "on": p["happened_on"].isoformat(), "outcome": p["outcome"],
        "reference_given": p["reference"], "resolved_late": p["late"]})
    return {"recorded": f"{c.reference}: {EVENTS[p['kind']].lower()}", **await overview(session, firm)}


async def staff_view(session: AsyncSession, firm: Firm, role: UserRole) -> dict:
    """What a role may see: the register for a reader, and for anyone else only that they may record."""
    if UserRole(role) in READERS:
        return {**await overview(session, firm), "can_record": UserRole(role) in RECEIVERS, "can_decide": UserRole(role) in DECIDERS}
    return {"applies": applies(firm), "complaints": [], "counts": {}, "can_record": UserRole(role) in RECEIVERS, "can_decide": False,
            "message": "The register is read by compliance, a branch leader and operations."}
