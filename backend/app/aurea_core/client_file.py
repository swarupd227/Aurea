"""The client file: a trusted contact, estate documents, and the life events that change a plan (L100 §5.3, §5.4, §5.8).

A person records each from a document or a meeting note, and nothing here is inferred. No document on file does not
mean none exists: an attorney may hold it, so the file says what it knows and what it does not.

The practical estate failure is documents and designations that no longer agree with a family's life, so that is what
is measured. An estate document signed before a marriage, divorce, birth or death is out of date, as is one past the
review age, and a beneficiary who has since died, or whom a recorded divorce has made a former spouse, is named. What
to do is for the client and their attorney.

A life event raises a task for a person, because each is a reason to look at the plan and the portfolio, and says
which of them it touches. It changes no figure itself.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, registration, tasks
from app.models.client_file import DOCUMENT_KINDS, LIFE_EVENTS, EstateDocument, LifeEvent, TrustedContact
from app.models.graph import Account, AccountBeneficiary, Household, Mandate, Person
from app.models.tenant import Firm
from app.provenance import ledger

REVIEW_YEARS = 5
TASK_DAYS = 30
EXPECTED_DOCUMENTS = ("will", "power_of_attorney", "health_directive")
# What an event makes out of date. A document signed before one of these may no longer say what the client means.
RESHAPES_AN_ESTATE = ("marriage", "divorce", "birth", "death_in_family")
TOUCHES = {
    "marriage": ["plan", "risk_profile", "beneficiaries", "estate_documents", "protection"],
    "divorce": ["plan", "risk_profile", "beneficiaries", "estate_documents", "protection", "accounts"],
    "birth": ["plan", "beneficiaries", "estate_documents", "protection", "education"],
    "death_in_family": ["plan", "beneficiaries", "estate_documents", "protection"],
    "inheritance": ["plan", "risk_profile", "ips", "tax"],
    "business_sale": ["plan", "risk_profile", "ips", "tax", "concentration"],
    "relocation": ["plan", "tax", "jurisdiction"],
    "job_change": ["plan", "protection", "equity_compensation"],
    "retirement": ["plan", "risk_profile", "ips", "income"],
    "other": ["plan"],
}


class ClientFileError(ValueError):
    """This cannot be recorded, with the reason."""


def _document(source: str | None) -> str:
    named = (source or "").strip()
    if len(named) < 5:
        raise ClientFileError("Say which document or meeting note this comes from.")
    return named


async def _person(session: AsyncSession, person_id: uuid.UUID, firm_id: uuid.UUID) -> Person:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id or person.household_id is None:
        raise ClientFileError("Person not found.")
    return person


async def _today(session: AsyncSession, firm_id: uuid.UUID) -> date:
    return firm_time.today(await session.get(Firm, firm_id))


def _people_view(people: list[Person], contacts: dict, documents: dict, events: list[LifeEvent], today: date) -> list[dict]:
    reshaping = [e for e in events if e.kind in RESHAPES_AN_ESTATE]
    rows = []
    for p in people:
        held = documents.get(p.id, {})
        papers = []
        for kind, doc in sorted(held.items()):
            after = [e for e in reshaping if e.happened_on > doc.signed_on and (e.person_id is None or e.person_id == p.id)]
            old = doc.signed_on < today - timedelta(days=365 * REVIEW_YEARS)
            papers.append({"id": str(doc.id), "kind": kind, "signed_on": doc.signed_on.isoformat(), "held_by": doc.held_by, "document": doc.source_document,
                           "out_of_date": bool(after) or old, "why": after[-1].kind if after else "age" if old else None})
        c = contacts.get(p.id)
        rows.append({"person_id": str(p.id), "name": p.full_name, "trusted_contact": {"name": c.name, "relationship": c.relationship, "phone": c.phone, "email": c.email, "as_of": c.as_of.isoformat(),
                                                                                       "document": c.source_document} if c else None,
                     "documents": papers, "not_on_file": [k for k in EXPECTED_DOCUMENTS if k not in held], "out_of_date": sum(1 for d in papers if d["out_of_date"])})
    return rows


async def _designation_issues(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID, people: list[Person], events: list[LifeEvent]) -> list[dict]:
    """Beneficiaries who have died or are a former spouse, and designations that do not add up."""
    audit = await registration.beneficiary_audit(session, firm_id, household_id=household_id)
    issues = [{"account": g["account_name"], "issue": g["issue"], "name": None} for g in audit["gaps"]]
    everyone = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    gone = {p.full_name.strip().lower() for p in everyone if p.deceased_on}
    gone |= {e.other_party.strip().lower() for e in events if e.kind == "death_in_family" and e.other_party}
    former = {e.other_party.strip().lower() for e in events if e.kind == "divorce" and e.other_party}
    accounts = (await session.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
        Mandate.person_id.in_([p.id for p in people] or [uuid.uuid4()]), Account.status == "active"))).scalars().all()
    if accounts:
        names = {a.id: a.name for a in accounts}
        for b in (await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id.in_(list(names))))).scalars().all():
            who = b.beneficiary_name.strip().lower()
            if who in gone:
                issues.append({"account": names[b.account_id], "issue": "beneficiary has died", "name": b.beneficiary_name})
            elif who in former:
                issues.append({"account": names[b.account_id], "issue": "beneficiary is a former spouse", "name": b.beneficiary_name})
    return issues


def _event_view(e: LifeEvent, today: date, names: dict) -> dict:
    return {"id": str(e.id), "kind": e.kind, "happened_on": e.happened_on.isoformat(), "person": names.get(e.person_id), "other_party": e.other_party, "note": e.note,
            "document": e.source_document, "touches": TOUCHES.get(e.kind, ["plan"]), "task_id": str(e.task_id) if e.task_id else None, "recent": e.happened_on >= today - timedelta(days=365)}


async def _load(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID]) -> tuple:
    people = (await session.execute(select(Person).where(Person.household_id.in_(household_ids), Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    ids = [p.id for p in people]
    contacts = {c.person_id: c for c in (await session.execute(select(TrustedContact).where(TrustedContact.person_id.in_(ids)).order_by(TrustedContact.created_at))).scalars().all()} if ids else {}
    documents: dict = defaultdict(dict)
    if ids:
        for d in (await session.execute(select(EstateDocument).where(EstateDocument.person_id.in_(ids)).order_by(EstateDocument.signed_on, EstateDocument.created_at))).scalars().all():
            documents[d.person_id][d.kind] = d
    events: dict = defaultdict(list)
    for e in (await session.execute(select(LifeEvent).where(LifeEvent.household_id.in_(household_ids)).order_by(LifeEvent.happened_on))).scalars().all():
        events[e.household_id].append(e)
    by_home: dict = defaultdict(list)
    for p in people:
        by_home[p.household_id].append(p)
    return by_home, contacts, documents, events


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    today = await _today(session, firm_id)
    by_home, contacts, documents, events = await _load(session, firm_id, [household_id])
    people, happened = by_home[household_id], events[household_id]
    names = {p.id: p.full_name for p in people}
    return {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "people": _people_view(people, contacts, documents, happened, today),
            "events": [_event_view(e, today, names) for e in reversed(happened)], "designation_issues": await _designation_issues(session, firm_id, household_id, people, happened),
            "review_years": REVIEW_YEARS, "kinds": {"documents": list(DOCUMENT_KINDS), "events": list(LIFE_EVENTS)}}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """What is missing or out of date, household by household."""
    today = await _today(session, firm_id)
    households = (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None)).order_by(Household.name))).scalars().all()
    by_home, contacts, documents, events = await _load(session, firm_id, [h.id for h in households] or [uuid.uuid4()])
    rows = []
    for h in households:
        people = _people_view(by_home[h.id], contacts, documents, events[h.id], today)
        rows.append({"household_id": str(h.id), "household": h.name, "people": len(people), "no_trusted_contact": sum(1 for p in people if not p["trusted_contact"]),
                     "documents_not_on_file": sum(len(p["not_on_file"]) for p in people), "out_of_date": sum(p["out_of_date"] for p in people),
                     "recent_events": sum(1 for e in events[h.id] if e.happened_on >= today - timedelta(days=365))})
    return {"households": rows, "as_of": today.isoformat(), "summary": {
        "households": len(rows), "with_no_trusted_contact": sum(1 for r in rows if r["no_trusted_contact"]), "documents_out_of_date": sum(r["out_of_date"] for r in rows),
        "recent_events": sum(r["recent_events"] for r in rows)}}


async def prepare_contact(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, name: str | None, relationship: str | None, source_document: str | None,
                          phone: str | None = None, email: str | None = None) -> dict:
    person = await _person(session, person_id, firm_id)
    who, tie = (name or "").strip(), (relationship or "").strip()
    if len(who) < 2:
        raise ClientFileError("Give the contact's name.")
    if who.lower() == person.full_name.strip().lower():
        raise ClientFileError("A trusted contact is someone other than the client.")
    if len(tie) < 2:
        raise ClientFileError("Say how they know the client.")
    number, mail = (phone or "").strip() or None, (email or "").strip() or None
    if not number and not mail:
        raise ClientFileError("Give a phone number or email, or the firm cannot reach them.")
    was = (await session.execute(select(TrustedContact).where(TrustedContact.person_id == person_id).order_by(TrustedContact.created_at.desc()).limit(1))).scalars().first()
    return {"person": person, "name": who, "relationship": tie, "phone": number, "email": mail, "document": _document(source_document), "was": was}


async def record_contact(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> TrustedContact:
    ready = await prepare_contact(session, person_id, firm_id=firm_id, **terms)
    row = TrustedContact(firm_id=firm_id, person_id=person_id, name=ready["name"], relationship=ready["relationship"], phone=ready["phone"], email=ready["email"],
                         as_of=await _today(session, firm_id), source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="trusted_contact_recorded", actor=by, content={"person_id": str(person_id), "document": row.source_document})
    return row


async def prepare_estate_document(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, signed_on: date | None, source_document: str | None,
                                  held_by: str | None = None) -> dict:
    person = await _person(session, person_id, firm_id)
    if kind not in DOCUMENT_KINDS:
        raise ClientFileError(f"The kind must be one of {', '.join(DOCUMENT_KINDS)}.")
    if signed_on is None:
        raise ClientFileError("Give the date it was signed.")
    if signed_on > await _today(session, firm_id):
        raise ClientFileError("It cannot be signed in the future.")
    return {"person": person, "kind": kind, "signed_on": signed_on, "held_by": (held_by or "").strip() or None, "document": _document(source_document)}


async def record_estate_document(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> EstateDocument:
    ready = await prepare_estate_document(session, person_id, firm_id=firm_id, **terms)
    row = EstateDocument(firm_id=firm_id, person_id=person_id, kind=ready["kind"], signed_on=ready["signed_on"], held_by=ready["held_by"], source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="estate_document_recorded", actor=by, content={"person_id": str(person_id), "kind": row.kind, "signed_on": row.signed_on.isoformat()})
    return row


async def prepare_event(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, happened_on: date | None, source_document: str | None,
                        person_id: uuid.UUID | None = None, other_party: str | None = None, note: str | None = None) -> dict:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ClientFileError("Household not found.")
    if kind not in LIFE_EVENTS:
        raise ClientFileError(f"The event must be one of {', '.join(LIFE_EVENTS)}.")
    if happened_on is None:
        raise ClientFileError("Give the date it happened.")
    if happened_on > await _today(session, firm_id):
        raise ClientFileError("A life event is recorded once it has happened.")
    person = await session.get(Person, person_id) if person_id else None
    if person_id and (person is None or person.household_id != household_id):
        raise ClientFileError("That person is not in this household.")
    return {"household": household, "person": person, "kind": kind, "day": happened_on, "other": (other_party or "").strip() or None, "note": (note or "").strip() or None,
            "document": _document(source_document), "touches": TOUCHES[kind]}


async def record_event(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, by_user: uuid.UUID, **terms) -> LifeEvent:
    ready = await prepare_event(session, household_id, firm_id=firm_id, **terms)
    today = await _today(session, firm_id)
    task = tasks.new(firm_id, f"Review after {ready['kind'].replace('_', ' ')}: {ready['household'].name}", household=ready["household"],
                     due_date=today + timedelta(days=TASK_DAYS), assignee=None, by=by_user)
    task.detail = f"Look at: {', '.join(t.replace('_', ' ') for t in ready['touches'])}."
    session.add(task)
    await session.flush()
    row = LifeEvent(firm_id=firm_id, household_id=household_id, person_id=ready["person"].id if ready["person"] else None, kind=ready["kind"], happened_on=ready["day"], other_party=ready["other"],
                    note=ready["note"], source_document=ready["document"], task_id=task.id, recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="life_event_recorded", actor=by, content={"household_id": str(household_id), "kind": row.kind, "happened_on": row.happened_on.isoformat()})
    return row
