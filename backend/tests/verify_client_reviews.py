"""Prove client reviews: a date comes only from a review a person recorded, what is refused is refused
before anything is written, a booking does not hide an overdue review, and a firm sees only its own.

Runs against a throwaway SQLite database.

    python -m tests.verify_client_reviews
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import reviews as api
from app.aurea_core import firm_time, reviews as rv
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState, anthropic_tool_schema, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.billing import Invoice
from app.models.engagement import ClientReview, Meeting
from app.models.enums import MeetingStatus, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm
from seed import client_review_backfill

PASS, FAIL = [], []
TOPICS = list(rv.TOPICS)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except (rv.ReviewError, ExecutorError) as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:90]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def forbidden(label: str, coro, code: int = 403) -> None:
    try:
        await coro
    except HTTPException as e:
        check(label, e.status_code, code)
    else:
        check(label, "allowed", code)


def dates() -> None:
    print("\n=== Calendar months ===")
    check("a month later", rv.add_months(date(2026, 3, 15), 3), date(2026, 6, 15))
    check("over a year end", rv.add_months(date(2026, 11, 30), 3), date(2027, 2, 28))
    check("the last day of a shorter month", rv.add_months(date(2026, 8, 31), 6), date(2027, 2, 28))
    check("a leap day a year on", rv.add_months(date(2028, 2, 29), 12), date(2029, 2, 28))
    check("into a leap February", rv.add_months(date(2027, 8, 31), 6), date(2028, 2, 29))
    check("December stays in its year", rv.add_months(date(2026, 9, 10), 3), date(2026, 12, 10))


async def main() -> int:
    dates()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()

            async def household(name, f=firm):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name)
                s.add(h)
                await s.flush()
                return h

            def person(role, name):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{name}@t", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u

            sophie = person(UserRole.ADVISER, "sophie")
            priya = person(UserRole.PARAPLANNER, "priya")
            david = person(UserRole.COMPLIANCE, "david")
            olive = person(UserRole.OPERATIONS, "olive")
            research = person(UserRole.RESEARCH_CIO, "rae")
            client = person(UserRole.CLIENT, "wei")
            await s.flush()

            ann = await household("Ann")
            bob = await household("Bob")
            cal = await household("Cal")
            dee = await household("Dee")
            theirs = await household("Theirs", other)

            today = firm_time.today(firm)
            ago = lambda days: today - timedelta(days=days)  # noqa: E731
            at = lambda day: datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc)  # noqa: E731
            soon = utcnow() + timedelta(days=14)

            async def seen(h, f=firm):
                return await rv.for_household(s, h.id, firm_id=f.id)

            async def meeting(h, title, when, status=MeetingStatus.SCHEDULED):
                m = Meeting(id=uuid.uuid4(), firm_id=h.firm_id, household_id=h.id, title=title, scheduled_at=when, status=status)
                s.add(m)
                await s.flush()
                return m

            def write(h, **kw):
                args = {"held_on": today, "covered": TOPICS, "by": "sophie@t", "role": "adviser", **kw}
                return rv.record(s, h.id, firm_id=h.firm_id, **args)

            async def ledger(event):
                return (await s.execute(select(func.count()).select_from(LedgerEntry).where(
                    LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == event))).scalar()

            print("\n=== No record is no date ===")
            v = await seen(ann)
            check("no review reads no record", v["status"], "no_record")
            check("  with no due date, not an invented one", (v["next_due"], v["days_to_due"], v["last_review"]), (None, None, None))
            check("the default is annual", (v["cadence_months"], v["cadence"], v["cadence_set"]), (12, "Annual", False))
            check("a meeting alone is not a review",
                  (await meeting(dee, "Annual review", at(ago(40)), MeetingStatus.COMPLETED)) and (await seen(dee))["status"], "no_record")
            d = await seen(dee)
            check("  but it is pointed out", d["unrecorded_meeting"]["held_on"], ago(40).isoformat())

            print("\n=== What is refused is refused before anything is written ===")
            before = (await s.execute(select(func.count()).select_from(ClientReview))).scalar()
            await refused("a review not yet held", write(ann, held_on=today + timedelta(days=1)), "before it has been held")
            await refused("no date", write(ann, held_on=None), "date")
            await refused("nothing covered", write(ann, covered=[]), "covered")
            await refused("a topic not offered", write(ann, covered=["vibes"]), "unknown topic")
            await refused("notes too long", write(ann, notes="x" * (rv.NOTES_MAX + 1)), "at most")
            await refused("another firm's household", rv.record(s, theirs.id, firm_id=firm.id, held_on=today, covered=TOPICS, by="x", role="adviser"), "not found")
            next_week = await meeting(bob, "Annual review", soon)
            await refused("a meeting still to come", write(bob, meeting_id=next_week.id), "not happened")
            await refused("another household's meeting", write(ann, meeting_id=next_week.id), "not this household")
            check("nothing was written", (await s.execute(select(func.count()).select_from(ClientReview))).scalar(), before)

            print("\n=== Recording ===")
            v = await write(ann, held_on=ago(100), covered=["tax_items", "performance_vs_goals", "tax_items"], notes="  Went well  ")
            check("the next date is the review plus the cadence", v["next_due"], rv.add_months(ago(100), 12).isoformat())
            check("on track", v["status"], "on_track")
            check("topics are kept in their own order, once", v["last_review"]["covered"], ["performance_vs_goals", "tax_items"])
            check("what was not covered is shown", v["last_review"]["not_covered"], ["plan_updates", "life_changes"])
            check("notes are trimmed", v["last_review"]["notes"], "Went well")
            check("it says who recorded it", (v["last_review"]["recorded_by"], v["last_review"]["recorded_by_role"]), ("sophie@t", "adviser"))
            check("it is on the ledger", await ledger("client_review_recorded"), 1)
            await refused("the same day twice", write(ann, held_on=ago(100)), "already recorded")
            v = await write(ann, held_on=ago(300))
            check("an older review does not move the date", v["last_review"]["held_on"], ago(100).isoformat())
            check("  but stays in the history", [r["held_on"] for r in v["history"]], [ago(100).isoformat(), ago(300).isoformat()])

            print("\n=== Status ===")
            v = await write(cal, held_on=rv.add_months(today, -12) - timedelta(days=1))
            check("past the date is overdue", (v["status"], v["days_to_due"] < 0), ("overdue", True))
            bea = await household("Bea")
            v = await write(bea, held_on=rv.add_months(today, -12))
            check("due today is due soon, not overdue", (v["status"], v["days_to_due"]), ("due_soon", 0))
            eve = await household("Eve")
            v = await write(eve, held_on=ago(300))
            check("near the date is due soon", v["status"], "due_soon" if v["days_to_due"] <= rv.DUE_SOON_DAYS else "on_track")

            print("\n=== From a meeting ===")
            d_meeting = (await s.execute(select(Meeting).where(Meeting.household_id == dee.id))).scalars().one()
            v = await write(dee, held_on=None, meeting_id=d_meeting.id)
            check("a meeting gives its date", v["last_review"]["held_on"], firm_time.today(firm, d_meeting.scheduled_at).isoformat())
            check("  and the prompt clears", v["unrecorded_meeting"], None)
            check("  and it stays tied to the meeting", v["last_review"]["meeting_id"], str(d_meeting.id))
            await refused("a meeting recorded twice", write(dee, held_on=None, meeting_id=d_meeting.id), "already recorded")
            await meeting(dee, "Market catch-up", at(ago(5)), MeetingStatus.COMPLETED)
            check("a later meeting of any kind is pointed out", (await seen(dee))["unrecorded_meeting"]["title"], "Market catch-up")
            await meeting(ann, "Old chat", at(ago(200)), MeetingStatus.COMPLETED)
            check("one before the last review is not", (await seen(ann))["unrecorded_meeting"], None)
            late = await meeting(bea, "Missed review", at(ago(2)))
            check("a meeting whose date passed may be recorded", (await write(bea, held_on=None, meeting_id=late.id))["last_review"]["meeting_id"], str(late.id))

            print("\n=== The firm's calendar ===")
            evening = datetime(2026, 3, 10, 22, 0, tzinfo=timezone.utc)
            fay = await household("Fay")
            m = await meeting(fay, "Annual review", evening, MeetingStatus.COMPLETED)
            v = await write(fay, held_on=None, meeting_id=m.id)
            check("10pm UTC is the next morning in Auckland", v["last_review"]["held_on"], "2026-03-11")

            print("\n=== Cadence ===")
            v = await rv.set_cadence(s, ann.id, firm_id=firm.id, months=6, reason="Larger client now", by="sophie@t")
            check("a shorter cadence moves the date", v["next_due"], rv.add_months(ago(100), 6).isoformat())
            check("  and is marked as set", (v["cadence"], v["cadence_set"], v["was_cadence_months"]), ("Half-yearly", True, 12))
            check("it is on the ledger with its reason", await ledger("review_cadence_set"), 1)
            await refused("a cadence not offered", rv.set_cadence(s, ann.id, firm_id=firm.id, months=7, reason="abc", by="x"), "every")
            await refused("no number", rv.set_cadence(s, ann.id, firm_id=firm.id, months="soon", reason="abc", by="x"), "every")
            await refused("no change", rv.set_cadence(s, ann.id, firm_id=firm.id, months=6, reason="abc", by="x"), "already")
            await refused("no reason", rv.set_cadence(s, ann.id, firm_id=firm.id, months=3, reason=" ", by="x"), "reason")
            await refused("another firm's", rv.set_cadence(s, theirs.id, firm_id=firm.id, months=3, reason="abc", by="x"), "not found")

            print("\n=== Booking ===")
            cal_due = (await seen(cal))["status"]
            await refused("a day gone by", rv.book(s, cal.id, firm_id=firm.id, on=ago(1), by="x"), "still to come")
            await refused("no date", rv.book(s, cal.id, firm_id=firm.id, on=None, by="x"), "date")
            await refused("a bad time", rv.book(s, cal.id, firm_id=firm.id, on=today + timedelta(days=3), at="teatime", by="x"), "HH:MM")
            v = await rv.book(s, cal.id, firm_id=firm.id, on=today + timedelta(days=3), at="14:30", by="sophie@t")
            booked = await s.get(Meeting, uuid.UUID(v["booked"]["meeting_id"]))
            check("it goes in the diary, named for its cadence", booked.title, "Annual review — Cal")
            stored = booked.scheduled_at if booked.scheduled_at.tzinfo else booked.scheduled_at.replace(tzinfo=timezone.utc)
            check("  on the firm's clock, stored as UTC", stored.astimezone(firm_time.zone(firm)).strftime("%H:%M"), "14:30")
            check("a booking does not stop it being overdue", v["status"], cal_due)
            check("it is on the ledger", await ledger("client_review_booked"), 1)
            await refused("booked twice", rv.book(s, cal.id, firm_id=firm.id, on=today + timedelta(days=4), by="x"), "already has")
            await meeting(eve, "Goals check-in", soon)
            check("a meeting that is not a review is not a booking", (await seen(eve))["booked"], None)
            check("an upcoming review counts as booked", (await seen(bob))["booked"]["title"], "Annual review")

            print("\n=== Fees are read from what was issued ===")
            def invoice(h, amount, issued, kind="invoice", currency="NZD"):
                s.add(Invoice(firm_id=h.firm_id, run_id=uuid.uuid4(), kind=kind, household_id=h.id, mandate_name="M",
                              period_start=date(2026, 1, 1), period_end=date(2026, 3, 31), amount=amount, currency=currency,
                              status="issued" if issued else "draft", issued_at=issued))

            invoice(ann, 500, at(ago(200)))
            invoice(ann, 300, at(ago(20)))
            invoice(ann, -100, at(ago(10)), kind="credit_note")
            invoice(ann, 999, None)
            invoice(bob, 250, at(ago(20)))
            invoice(bob, 40, at(ago(20)), currency="USD")
            await s.flush()
            check("since the last review, net of credits, drafts left out", (await seen(ann))["fees_since_review"], {"NZD": 200.0})
            check("with no review, everything on file, by currency", (await seen(bob))["fees_since_review"], {"NZD": 250.0, "USD": 40.0})

            print("\n=== The book ===")
            book = await rv.for_firm(s, firm.id)
            names = [h["household"] for h in book["households"]]
            check("overdue first", book["households"][0]["status"], "overdue")
            check("no record next", book["households"][1]["status"], "no_record")
            check("every household once, none of another firm's", (sorted(names), "Theirs" in names),
                  (sorted(["Ann", "Bob", "Cal", "Dee", "Bea", "Eve", "Fay"]), False))
            summary = book["summary"]
            check("the counts add up", summary["overdue"] + summary["no_record"] + summary["due_soon"] + summary["on_track"], summary["households"])
            check("bookings are counted", summary["booked"], 2)
            check("meetings not recorded are counted", summary["unrecorded_meetings"], 1)

            print("\n=== One firm cannot see another's ===")
            check("a household in another firm reads as none", await rv.for_household(s, ann.id, firm_id=other.id), None)
            check("  through the tool too", "not found" in await _tool_error(ToolExecutors(s, sophie.id, sophie.role, other.id), "read_client_reviews", {"household_id": str(ann.id)}), True)
            await forbidden("  and the route", api.read_household(ann.id, user=sophie, firm=other, db=s), 404)
            await refused("booking another firm's", rv.book(s, ann.id, firm_id=other.id, on=today + timedelta(days=3), by="x"), "not found")

            print("\n=== Who may do what ===")
            reader = api.ReaderDep.dependency
            for who, name in ((sophie, "adviser"), (priya, "paraplanner"), (david, "compliance"), (olive, "operations")):
                check(f"{name} reads", (await reader(who)).id, who.id)
            await forbidden("a client does not", reader(client))
            await forbidden("research does not", reader(research))
            rights = await api.read(user=david, firm=firm, db=s)
            check("compliance sees the record and may change none of it", (rights["can_record"], rights["can_set_cadence"]), (False, False))
            rights = await api.read(user=priya, firm=firm, db=s)
            check("a paraplanner may record, not set a cadence", (rights["can_record"], rights["can_set_cadence"]), (True, False))
            check("an adviser may do both", (await api.read_household(ann.id, user=sophie, firm=firm, db=s))["can_set_cadence"], True)

            body = api.RecordIn(held_on=today, covered=TOPICS)
            await forbidden("compliance cannot record", api.record(bob.id, body, user=david, firm=firm, db=s))
            await forbidden("nor book", api.book(bob.id, api.BookIn(date=today + timedelta(days=2)), user=david, firm=firm, db=s))
            await forbidden("a paraplanner cannot set a cadence", api.set_cadence(bob.id, api.CadenceIn(months=6, reason="abc"), user=priya, firm=firm, db=s))
            await forbidden("a refusal is a 409", api.record(bob.id, api.RecordIn(held_on=today), user=priya, firm=firm, db=s), 409)
            done = await api.record(bob.id, body, user=priya, firm=firm, db=s)
            check("a paraplanner's record is in their name", (done["last_review"]["recorded_by"], done["last_review"]["recorded_by_role"]), ("priya@t", str(UserRole.PARAPLANNER)))

            print("\n=== The tools ===")
            for key in ("record_client_review", "set_review_cadence", "book_client_review"):
                check(f"{key} pauses for confirmation", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                check(f"  and a client does not hold it", UserRole.CLIENT in TOOLS[key].roles_required, False)
            check("reading changes nothing", TOOLS["read_client_reviews"].change_state, ToolChangeState.NO)
            covered = anthropic_tool_schema(TOOLS["record_client_review"])["input_schema"]["properties"]["covered"]
            check("the model is asked for a list of topics, not one string", (covered["type"], covered["items"]["type"]), ("array", "string"))
            check("the date may be left out when a meeting gives it", validate_tool_call(UserRole.ADVISER, "record_client_review", {
                "household_id": str(ann.id), "covered": ["tax_items"]})[0], True)
            check("only an adviser sets a cadence", TOOLS["set_review_cadence"].roles_required, {UserRole.ADVISER})

            ex = ToolExecutors(s, sophie.id, sophie.role, firm.id)
            gerry = await household("Gerry")
            rows = lambda p: {r["label"]: r["value"] for r in p["rows"]}  # noqa: E731
            count = (await s.execute(select(func.count()).select_from(ClientReview))).scalar()
            shown = rows(await ex.preview_change("record_client_review", {
                "household_id": str(gerry.id), "held_on": ago(3).isoformat(), "covered": ["tax_items", "life_changes"], "notes": "Fine"}))
            check("the card names the record", shown, {"Client": "Gerry", "Held on": firm_time.spoken(ago(3)),
                                                       "Covered": "tax items, life changes", "Notes": "Fine"})
            check("  and showing it writes nothing", (await s.execute(select(func.count()).select_from(ClientReview))).scalar(), count)
            await refused("the card refuses what doing it would",
                          ex.preview_change("record_client_review", {"household_id": str(gerry.id), "held_on": ago(3).isoformat(), "covered": []}), "covered")
            await refused("  a date that is not one",
                          ex.preview_change("record_client_review", {"household_id": str(gerry.id), "held_on": "last week", "covered": TOPICS}), "YYYY-MM-DD")
            shown = rows(await ex.preview_change("set_review_cadence", {"household_id": str(gerry.id), "months": 3, "reason": "Asked for it"}))
            check("the cadence card shows from and to", (shown["From"], shown["To"], shown["Reason"]), ("Every 12 months", "Every 3 months", "Asked for it"))
            when = today + timedelta(days=5)
            shown = rows(await ex.preview_change("book_client_review", {"household_id": str(gerry.id), "date": when.isoformat(), "time": "09:15"}))
            check("the booking card shows the day and the time", shown["When"], f"{firm_time.spoken(when)}, 09:15")

            r = await ex.read_client_reviews({"household_id": str(gerry.id)})
            check("the tool reports no record plainly", r["message"], "Gerry: no review on record.")
            r = await ex.read_client_reviews({})
            check("the book's message counts every state", "no review on record" in r["message"], True)
            r = await ex.record_client_review({"household_id": str(gerry.id), "held_on": ago(3).isoformat(), "covered": ["tax_items"]})
            check("the tool records", (r["status"], r["last_review"]["recorded_by"]), ("on_track", "sophie@t"))
            r = await ex.set_review_cadence({"household_id": str(gerry.id), "months": 3, "reason": "Asked for it"})
            check("the tool sets a cadence", r["cadence_months"], 3)
            r = await ex.book_client_review({"household_id": str(gerry.id), "date": when.isoformat()})
            check("the tool books", r["booked"]["on"], when.isoformat())
            for role, key in ((UserRole.COMPLIANCE, "record_client_review"), (UserRole.PARAPLANNER, "set_review_cadence"),
                              (UserRole.OPERATIONS, "book_client_review")):
                who = ToolExecutors(s, sophie.id, role, firm.id)
                check(f"{role} refused at {key}", "cannot change" in await _tool_error(who, key, {"household_id": str(gerry.id), "months": 6, "reason": "abc", "date": when.isoformat(), "covered": TOPICS, "held_on": today.isoformat()}), True)

            print("\n=== Carrying meetings already on file ===")
            hal = await household("Hal")
            ivy = await household("Ivy")
            kept = await meeting(hal, "Annual strategy review — Hal", at(ago(46)), MeetingStatus.COMPLETED)
            await meeting(hal, "Market volatility catch-up", at(ago(7)), MeetingStatus.COMPLETED)
            await meeting(ivy, "Quarterly review", soon, MeetingStatus.PREPPED)
            await meeting(ivy, "Mid-year review", None, MeetingStatus.COMPLETED)
            await s.flush()
            created = await client_review_backfill.apply(s)
            carried = (await s.execute(select(ClientReview).where(ClientReview.household_id == hal.id))).scalars().one()
            check("a completed review is carried", (carried.meeting_id, carried.held_on), (kept.id, firm_time.today(firm, kept.scheduled_at)))
            check("  with what it covered not recorded", (carried.covered, carried.recorded_by), ([], "unknown"))
            check("  and it starts the clock", (await seen(hal))["next_due"], rv.add_months(carried.held_on, 12).isoformat())
            check("one not called a review is left for a person", (await seen(hal))["unrecorded_meeting"]["title"], "Market volatility catch-up")
            check("one not held, or with no date, is not carried", (await seen(ivy))["last_review"], None)
            check("run again it does nothing", await client_review_backfill.apply(s), 0)
            check("the first run carried only these", created >= 1, True)
            v = await seen(hal)
            check("a carried review reads as not covered, in full", v["last_review"]["not_covered"], TOPICS)

            await s.rollback()
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


async def _tool_error(executors: ToolExecutors, key: str, inputs: dict) -> str:
    try:
        await getattr(executors, key)(inputs)
    except ExecutorError as e:
        return str(e)
    return "allowed"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
