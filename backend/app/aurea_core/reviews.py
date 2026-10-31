"""Client reviews: when each household was last reviewed, and when the next is due.

A firm is expected to show ongoing advice for ongoing fees (L100 §5.10), so a review is evidence, and
only a person records it: when it was held and what it covered, append-only. A meeting is not a review
until someone says so, because a catch-up about the markets is not one. The one exception is
`seed/client_review_backfill.py`, which carries meetings already on file and marks what they covered as
not recorded.

The next date comes from a recorded review and the household's cadence, nothing else, so a household
with no review on record has no date. That is "no record", and no record does not mean no review: the
firm may hold one elsewhere. A booked meeting does not stop a review being overdue; it sits beside it.

Fees since the last review are what puts this in front of an examiner, so they are read from issued
invoices, credit notes included, and never estimated.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.core.db import utcnow
from app.models.billing import Invoice
from app.models.engagement import ClientReview, Meeting
from app.models.enums import MeetingStatus
from app.models.graph import Household
from app.models.tenant import Firm
from app.provenance import ledger

DEFAULT_CADENCE_MONTHS = 12
CADENCES = {3: "Quarterly", 6: "Half-yearly", 12: "Annual"}
DUE_SOON_DAYS = 30
# What the guide says a review covers: performance against goals, the plan, tax, and any life changes.
TOPICS = ("performance_vs_goals", "plan_updates", "tax_items", "life_changes")
NOTES_MAX = 2000
SHOWN_HISTORY = 10
ORDER = ("overdue", "no_record", "due_soon", "on_track")


class ReviewError(ValueError):
    """A review that cannot be recorded, booked or rescheduled, with the reason."""


def add_months(day: date, months: int) -> date:
    """The same day of a later month, or that month's last day when it is shorter."""
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def cadence_of(household: Household) -> int:
    return household.review_cadence_months or DEFAULT_CADENCE_MONTHS


def is_review(meeting: Meeting) -> bool:
    """Whether a meeting is booked as a review. Its title is the only thing that says so."""
    return "review" in (meeting.title or "").lower()


def _status(next_due: date | None, today: date) -> str:
    if next_due is None:
        return "no_record"
    if next_due < today:
        return "overdue"
    return "due_soon" if (next_due - today).days <= DUE_SOON_DAYS else "on_track"


def _review_view(r: ClientReview) -> dict:
    return {
        "id": str(r.id), "held_on": r.held_on.isoformat(), "covered": list(r.covered or []),
        "not_covered": [t for t in TOPICS if t not in (r.covered or [])], "notes": r.notes,
        "meeting_id": str(r.meeting_id) if r.meeting_id else None,
        "recorded_by": r.recorded_by, "recorded_by_role": r.recorded_by_role,
    }


async def _household(session: AsyncSession, household_id: uuid.UUID, firm_id: uuid.UUID) -> Household:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ReviewError("Household not found.")
    return household


def _view(household: Household, reviews: list[ClientReview], meetings: list[Meeting], invoices: list[Invoice],
          firm: Firm | None, today: date, now: datetime) -> dict:
    ranked = sorted(reviews, key=lambda r: r.held_on, reverse=True)
    last = ranked[0] if ranked else None
    cadence = cadence_of(household)
    next_due = add_months(last.held_on, cadence) if last else None

    upcoming = sorted((m for m in meetings if is_review(m) and m.scheduled_at and not m.date_passed(now)
                       and str(m.status) != MeetingStatus.COMPLETED.value), key=lambda m: m.scheduled_at)
    booked = upcoming[0] if upcoming else None

    # A meeting has been held since the last review and nobody has said whether it was one.
    linked = {r.meeting_id for r in reviews}
    held = sorted(((firm_time.today(firm, m.scheduled_at), m) for m in meetings
                   if str(m.status) == MeetingStatus.COMPLETED.value and m.scheduled_at and m.id not in linked),
                  key=lambda pair: pair[0], reverse=True)
    unrecorded = next(((day, m) for day, m in held if last is None or day > last.held_on), None)

    fees: dict[str, float] = {}
    for i in invoices:
        if i.issued_at and (last is None or firm_time.today(firm, i.issued_at) > last.held_on):
            fees[i.currency] = round(fees.get(i.currency, 0) + float(i.amount or 0), 2)

    return {
        "household_id": str(household.id), "household": household.name, "status": _status(next_due, today),
        "cadence_months": cadence, "cadence": CADENCES.get(cadence, f"Every {cadence} months"),
        "cadence_set": household.review_cadence_months is not None,
        "last_review": _review_view(last) if last else None,
        "next_due": next_due.isoformat() if next_due else None,
        "days_to_due": (next_due - today).days if next_due else None,
        "booked": {"meeting_id": str(booked.id), "title": booked.title,
                   "on": firm_time.today(firm, booked.scheduled_at).isoformat()} if booked else None,
        "unrecorded_meeting": {"meeting_id": str(unrecorded[1].id), "title": unrecorded[1].title,
                               "held_on": unrecorded[0].isoformat()} if unrecorded else None,
        "fees_since_review": fees,
        "history": [_review_view(r) for r in ranked[:SHOWN_HISTORY]],
    }


async def _rows(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID]) -> tuple[dict, dict, dict]:
    def by_household(rows) -> dict:
        out: dict[uuid.UUID, list] = {}
        for row in rows:
            out.setdefault(row.household_id, []).append(row)
        return out

    async def of(model) -> dict:
        return by_household((await session.execute(select(model).where(
            model.firm_id == firm_id, model.household_id.in_(household_ids)))).scalars().all())

    return await of(ClientReview), await of(Meeting), await of(Invoice)


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    reviews, meetings, invoices = await _rows(session, firm_id, [household_id])
    return {
        **_view(household, reviews.get(household_id, []), meetings.get(household_id, []),
                invoices.get(household_id, []), firm, firm_time.today(firm), utcnow()),
        "topics": list(TOPICS),
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    firm = await session.get(Firm, firm_id)
    households = (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()
    reviews, meetings, invoices = await _rows(session, firm_id, [h.id for h in households])
    today, now = firm_time.today(firm), utcnow()
    rows = [_view(h, reviews.get(h.id, []), meetings.get(h.id, []), invoices.get(h.id, []), firm, today, now)
            for h in households]
    rows.sort(key=lambda r: (ORDER.index(r["status"]), r["days_to_due"] if r["days_to_due"] is not None else 0, r["household"]))

    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    return {
        "households": rows,
        "summary": {
            "households": len(rows), "overdue": count("overdue"), "no_record": count("no_record"),
            "due_soon": count("due_soon"), "on_track": count("on_track"),
            "booked": sum(1 for r in rows if r["booked"]),
            "unrecorded_meetings": sum(1 for r in rows if r["unrecorded_meeting"]),
        },
        "topics": list(TOPICS), "cadences": sorted(CADENCES), "default_cadence_months": DEFAULT_CADENCE_MONTHS,
        "due_soon_days": DUE_SOON_DAYS, "as_of": today.isoformat(),
    }


async def prepare_record(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, held_on: date | None,
                         covered: list[str] | None, notes: str | None = None,
                         meeting_id: uuid.UUID | None = None) -> dict:
    """Everything recording would refuse, refused without writing, so a Confirm card that would only
    fail is not shown. Returns what would be written."""
    household = await _household(session, household_id, firm_id)
    firm = await session.get(Firm, firm_id)
    meeting = None
    if meeting_id:
        meeting = await session.get(Meeting, meeting_id)
        if meeting is None or meeting.firm_id != firm_id or meeting.household_id != household_id:
            raise ReviewError("That meeting is not this household's.")
        if str(meeting.status) != MeetingStatus.COMPLETED.value and not meeting.date_passed(utcnow()):
            raise ReviewError("That meeting has not happened yet, so it cannot be recorded as a review.")
        if (await session.execute(select(ClientReview.id).where(ClientReview.meeting_id == meeting_id))).first():
            raise ReviewError("That meeting is already recorded as a review.")
        if held_on is None and meeting.scheduled_at:
            held_on = firm_time.today(firm, meeting.scheduled_at)
    if held_on is None:
        raise ReviewError("Give the date the review was held.")
    if held_on > firm_time.today(firm):
        raise ReviewError("A review cannot be recorded before it has been held. Book it instead.")

    picked = list(dict.fromkeys(str(t).strip().lower() for t in covered or []))
    unknown = [t for t in picked if t not in TOPICS]
    if unknown:
        raise ReviewError(f"Unknown topic: {', '.join(unknown)}. Choose from {', '.join(TOPICS)}.")
    if not picked:
        raise ReviewError("Say what the review covered. That is what makes it evidence.")
    text = (notes or "").strip() or None
    if text and len(text) > NOTES_MAX:
        raise ReviewError(f"Notes can be at most {NOTES_MAX} characters.")
    if (await session.execute(select(ClientReview.id).where(
            ClientReview.household_id == household_id, ClientReview.held_on == held_on))).first():
        raise ReviewError(f"A review on {held_on.isoformat()} is already recorded for {household.name}.")
    return {"household": household, "meeting": meeting, "held_on": held_on,
            "covered": [t for t in TOPICS if t in picked], "notes": text}


async def record(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, held_on: date | None,
                 covered: list[str] | None, notes: str | None = None, meeting_id: uuid.UUID | None = None,
                 by: str, role: str) -> dict:
    ready = await prepare_record(session, household_id, firm_id=firm_id, held_on=held_on, covered=covered,
                                 notes=notes, meeting_id=meeting_id)
    review = ClientReview(firm_id=firm_id, household_id=household_id, meeting_id=meeting_id, held_on=ready["held_on"],
                          covered=ready["covered"], notes=ready["notes"], recorded_by=by, recorded_by_role=str(role))
    session.add(review)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_review_recorded", actor=by, content={
        "review_id": str(review.id), "household_id": str(household_id), "held_on": ready["held_on"].isoformat(),
        "covered": ready["covered"], "meeting_id": str(meeting_id) if meeting_id else None,
    })
    view = await for_household(session, household_id, firm_id=firm_id)
    return {**view, "review_id": str(review.id)}


async def prepare_cadence(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, months,
                          reason: str | None) -> dict:
    household = await _household(session, household_id, firm_id)
    try:
        wanted = int(months)
    except (TypeError, ValueError):
        wanted = 0
    if wanted not in CADENCES:
        raise ReviewError(f"A cadence is every {', '.join(str(c) for c in sorted(CADENCES))} months.")
    if household.review_cadence_months == wanted:
        raise ReviewError(f"{household.name} is already reviewed every {wanted} months.")
    text = (reason or "").strip()
    if len(text) < 3:
        raise ReviewError("Give the reason for this cadence.")
    return {"household": household, "was": cadence_of(household), "months": wanted, "reason": text}


async def set_cadence(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, months, reason: str | None,
                      by: str) -> dict:
    ready = await prepare_cadence(session, household_id, firm_id=firm_id, months=months, reason=reason)
    ready["household"].review_cadence_months = ready["months"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="review_cadence_set", actor=by, content={
        "household_id": str(household_id), "was": ready["was"], "months": ready["months"], "reason": ready["reason"],
    })
    view = await for_household(session, household_id, firm_id=firm_id)
    return {**view, "was_cadence_months": ready["was"]}


async def prepare_booking(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, on: date | None,
                          at: str | None = None) -> dict:
    household = await _household(session, household_id, firm_id)
    firm = await session.get(Firm, firm_id)
    if on is None:
        raise ReviewError("Give the date to book the review for.")
    try:
        clock = time.fromisoformat(at or "10:00")
    except ValueError:
        raise ReviewError("Give the time as HH:MM.")
    when = datetime.combine(on, clock, tzinfo=firm_time.zone(firm))
    if when <= utcnow():
        raise ReviewError("A review can only be booked for a time still to come.")
    view = await for_household(session, household_id, firm_id=firm_id)
    if view["booked"]:
        raise ReviewError(f"{household.name} already has '{view['booked']['title']}' booked for {view['booked']['on']}.")
    return {"household": household, "when": when, "title": f"{CADENCES.get(cadence_of(household), 'Client')} review — {household.name}"}


async def book(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, on: date | None,
               at: str | None = None, by: str) -> dict:
    ready = await prepare_booking(session, household_id, firm_id=firm_id, on=on, at=at)
    meeting = Meeting(firm_id=firm_id, household_id=household_id, title=ready["title"],
                      scheduled_at=ready["when"].astimezone(timezone.utc))
    session.add(meeting)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_review_booked", actor=by, content={
        "household_id": str(household_id), "meeting_id": str(meeting.id), "on": ready["when"].isoformat(),
    })
    return await for_household(session, household_id, firm_id=firm_id)
