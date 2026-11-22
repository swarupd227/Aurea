"""The client file: a trusted contact, estate documents, and the life events that change a plan (L100 §5.3, §5.4, §5.8).

A person records each from a document or a meeting note, and nothing here is inferred. No document on file does not
mean none exists: an attorney may hold it, so the file says what it knows and what it does not.

The practical estate failure is documents and designations that no longer agree with a family's life, so that is what
is measured. An estate document signed before a marriage, divorce, birth or death is out of date, as is one past the
review age, and a beneficiary who has since died, or whom a recorded divorce has made a former spouse, is named. What
to do is for the client and their attorney.

A life event raises a task for a person, because each is a reason to look at the plan and the portfolio, and says
which of them it touches. It changes no figure itself.

Three estate gaps are read from the same records (B-185). A revocable trust on file with no account registered to a trust
"may be unfunded": the deed is on file, the money may not be in it. A household with a minor and no will on file for any
adult is named, because a will is where a guardian is nominated. And the titling-fix list: each account whose
registration or beneficiaries disagree with what is on file. They go to the client's recorded adviser as one task, through
a Confirm card; nothing here retitles an account or changes a designation, and nothing is sent to an attorney.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, nominations, registration, tasks
from app.models.client_file import DOCUMENT_KINDS, LIFE_EVENTS, EstateDocument, LifeEvent, TrustedContact
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
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


# B-185. Under this age a person is a minor. It is an assumption, named in the result: the age of majority is set by the
# state or country, and in some it is 19 or 21.
MINOR_UNDER = 18
# Registrations that hold an account in a person's own name, which a revocable trust could own instead. A retirement
# account is never retitled to a trust: it passes by its beneficiaries.
IN_OWN_NAME = ("individual", "joint_jtwros", "joint_tic", "joint_tbe", "community_property")
TRUST_DOCUMENTS = ("revocable_trust", "trust_deed")
ESTATE_REVIEW_SOURCE = "estate_review"
NOT_ON_FILE = "Not on file does not mean none exists: an attorney or the custodian may hold it."
# What each designation issue was found from.
_ISSUE_EVIDENCE = {"beneficiary has died": "a death on file", "beneficiary is a former spouse": "a divorce on file",
                   "no beneficiaries on file": "the designations on file"}


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
        nominees: dict = defaultdict(list)
        for b in (await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id.in_(list(names))))).scalars().all():
            nominees[b.account_id].append(b.beneficiary_name)
            who = b.beneficiary_name.strip().lower()
            if who in gone:
                issues.append({"account": names[b.account_id], "issue": "beneficiary has died", "name": b.beneficiary_name})
            elif who in former:
                issues.append({"account": names[b.account_id], "issue": "beneficiary is a former spouse", "name": b.beneficiary_name})
        firm = await session.get(Firm, firm_id)
        if nominations.cap_for(firm) is not None:
            # India: every demat wants a nomination, and a nominee is read against the owner's will (aurea_core/nominations.py).
            owners = {m.id: m.person_id for m in (await session.execute(select(Mandate).where(
                Mandate.id.in_([a.mandate_id for a in accounts])))).scalars().all()}
            wills = {d.person_id: d for d in (await session.execute(select(EstateDocument).where(
                EstateDocument.person_id.in_([p.id for p in people] or [uuid.uuid4()]), EstateDocument.kind == "will")
                .order_by(EstateDocument.signed_on))).scalars().all()}
            for a in accounts:
                if not nominees.get(a.id):
                    issues.append({"account": a.name, "issue": "no nomination on file (an opt-out is not modelled)", "name": None})
                    continue
                will = wills.get(owners.get(a.mandate_id))
                issues += nominations.will_issues(a.name, nominees[a.id], will.named if will else None)
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
    issues = await _designation_issues(session, firm_id, household_id, people, happened)
    return {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "people": _people_view(people, contacts, documents, happened, today),
            "events": [_event_view(e, today, names) for e in reversed(happened)], "designation_issues": issues,
            "estate_gaps": await _estate_gaps(session, firm_id, household_id, people, documents, issues, today),
            "review_years": REVIEW_YEARS, "kinds": {"documents": list(DOCUMENT_KINDS), "events": list(LIFE_EVENTS)}}


def _age(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def _estate_gaps(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID, people: list[Person], documents: dict,
                       issues: list[dict], today: date) -> dict:
    """An unfunded trust, a minor with no will on file, and the titling-fix list, from what is on file only (B-185)."""
    ages = {p.id: _age(p.date_of_birth, today) for p in people}
    minors = [p for p in people if ages[p.id] is not None and ages[p.id] < MINOR_UNDER]
    grown = [p for p in people if p not in minors]
    held_docs = {p.id: documents.get(p.id, {}) for p in people}
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id, LegalEntity.firm_id == firm_id))).scalars().all()
    trust_entities = {e.id for e in entities if str(e.entity_type) == "trust"}
    owners = [c for c in (Mandate.person_id.in_([p.id for p in people]) if people else None,
                          Mandate.entity_id.in_([e.id for e in entities]) if entities else None) if c is not None]
    held = (await session.execute(select(Account, Mandate).join(Mandate, Mandate.id == Account.mandate_id).where(
        Account.firm_id == firm_id, Account.status == "active", or_(*owners)).order_by(Account.name))).all() if owners else []
    in_trust = [a for a, m in held if a.registration_type == "trust" or (m.entity_id is not None and m.entity_id in trust_entities)]

    trusts = [{"person_id": str(p.id), "person": p.full_name, "signed_on": d.signed_on.isoformat(), "document": d.source_document,
               "status": "accounts_registered" if in_trust else "may_be_unfunded", "accounts": [a.name for a in in_trust]}
              for p in people if (d := held_docs[p.id].get("revocable_trust"))]
    guardianship = None
    if minors:
        wills = [p.full_name for p in grown if "will" in held_docs[p.id]]
        guardianship = {"minors": [{"name": p.full_name, "age": ages[p.id]} for p in minors], "wills_on_file": wills,
                        "no_will_on_file": not wills, "adults": [p.full_name for p in grown]}

    fixes: list[dict] = []
    trust_of = {p.id: held_docs[p.id]["revocable_trust"] for p in people if "revocable_trust" in held_docs[p.id]}
    for a, m in held:
        if m.person_id in trust_of and a.registration_type in IN_OWN_NAME:
            fixes.append({"account_id": str(a.id), "account": a.name, "registration": a.registration_type, "issue": "titled outside the revocable trust",
                          "name": None, "evidence": f"revocable trust signed {trust_of[m.person_id].signed_on.isoformat()}"})
    if not trust_entities and not any(k in held_docs[p.id] for p in people for k in TRUST_DOCUMENTS):
        for a, _m in held:
            if a.registration_type == "trust":
                fixes.append({"account_id": str(a.id), "account": a.name, "registration": a.registration_type,
                              "issue": "registered to a trust with no trust document on file", "name": None, "evidence": "the account's registration"})
    young = {p.full_name.strip().lower(): p for p in minors}
    if held and young:
        by_id = {a.id: a for a, _m in held}
        for b in (await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id.in_(list(by_id)))
                                        .order_by(AccountBeneficiary.beneficiary_name))).scalars().all():
            child = young.get(b.beneficiary_name.strip().lower())
            if child is not None:
                a = by_id[b.account_id]
                fixes.append({"account_id": str(a.id), "account": a.name, "registration": a.registration_type, "issue": "beneficiary is a minor",
                              "name": b.beneficiary_name, "evidence": f"date of birth on file, age {ages[child.id]}"})
    registered = {a.name: a for a, _m in held}
    for i in issues:
        a = registered.get(i["account"])
        fixes.append({"account_id": str(a.id) if a else None, "account": i["account"], "registration": a.registration_type if a else None,
                      "issue": i["issue"], "name": i["name"], "evidence": _ISSUE_EVIDENCE.get(i["issue"], "the designations on file")})
    return {"trusts": trusts, "may_be_unfunded": sum(1 for t in trusts if t["status"] == "may_be_unfunded"), "guardianship": guardianship,
            "titling_fixes": fixes, "ages_not_on_file": [p.full_name for p in people if ages[p.id] is None],
            "assumed": [f"minor_under_{MINOR_UNDER}"], "not_on_file": NOT_ON_FILE}


def _to_raise(gaps: dict) -> tuple[list[dict], dict | None, list[dict]]:
    unfunded = [t for t in gaps["trusts"] if t["status"] == "may_be_unfunded"]
    guardian = gaps["guardianship"] if gaps["guardianship"] and gaps["guardianship"]["no_will_on_file"] else None
    return unfunded, guardian, gaps["titling_fixes"]


async def prepare_estate_review(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    """What raising the estate gaps for the adviser would write, refused without writing. It goes to whoever is on
    record as the client's adviser (advisers.adviser_of), never someone worked out, and unassigned says so."""
    from app.aurea_core import advisers

    view = await for_household(session, household_id, firm_id=firm_id)
    if view is None:
        raise ClientFileError("Household not found.")
    unfunded, guardian, fixes = _to_raise(view["estate_gaps"])
    if not (unfunded or guardian or fixes):
        raise ClientFileError(f"Nothing on file for {view['household']} to raise: no trust that may be unfunded, no minor without a will on file, "
                              f"no account to retitle. {NOT_ON_FILE}")
    household = await session.get(Household, household_id)
    title = f"Estate review: {household.name}"[:tasks.TITLE_MAX]
    waiting = (await session.execute(select(Task).where(Task.firm_id == firm_id, Task.household_id == household_id, Task.title == title,
                                                        Task.source == ESTATE_REVIEW_SOURCE, Task.status == TaskStatus.OPEN))).scalars().first()
    if waiting is not None:
        raise ClientFileError(f"The estate review for {household.name} is already an open task. Close it before raising another.")
    adviser = await advisers.adviser_of(session, firm_id, household_id)
    adviser = adviser if adviser is not None and adviser.is_active else None
    lines = [f"May be unfunded: {t['person']}'s revocable trust, signed {t['signed_on']}. No account is registered to a trust." for t in unfunded]
    if guardian:
        lines.append(f"No will on file for {', '.join(guardian['adults']) or 'any adult'}, with a minor in the household: "
                     f"{', '.join(m['name'] for m in guardian['minors'])}. A will is where a guardian is named.")
    lines += [f"Titling: {f['account']} - {f['issue']}{', ' + f['name'] if f['name'] else ''} ({f['evidence']})." for f in fixes]
    lines.append(NOT_ON_FILE + " What to change is for the client and their attorney.")
    today = await _today(session, firm_id)
    return {"household": household, "adviser": adviser, "title": title, "detail": "\n".join(lines), "due_date": today + timedelta(days=TASK_DAYS),
            "unfunded": unfunded, "guardianship": guardian, "fixes": fixes}


async def raise_estate_review(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, by_user: uuid.UUID) -> Task:
    """One task for the client's adviser carrying the estate gaps. It retitles nothing, changes no designation and
    contacts nobody. There is no referral draft on the platform, so a referral to an attorney is the adviser's to write."""
    ready = await prepare_estate_review(session, household_id, firm_id=firm_id)
    adviser = ready["adviser"]
    task = Task(firm_id=firm_id, household_id=household_id, title=ready["title"], detail=ready["detail"], status=TaskStatus.OPEN,
                due_date=ready["due_date"], source=ESTATE_REVIEW_SOURCE, subject_label=ready["household"].name,
                assigned_to=adviser.id if adviser else None, assigned_by=by_user if adviser else None)
    session.add(task)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="estate_review_raised", actor=by, content={
        "household_id": str(household_id), "task_id": str(task.id), "may_be_unfunded": len(ready["unfunded"]),
        "minor_without_will": bool(ready["guardianship"]), "titling_fixes": len(ready["fixes"])})
    return task


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
                                  held_by: str | None = None, named_heirs=None) -> dict:
    person = await _person(session, person_id, firm_id)
    if kind not in DOCUMENT_KINDS:
        raise ClientFileError(f"The kind must be one of {', '.join(DOCUMENT_KINDS)}.")
    if signed_on is None:
        raise ClientFileError("Give the date it was signed.")
    if signed_on > await _today(session, firm_id):
        raise ClientFileError("It cannot be signed in the future.")
    heirs = [h.strip() for h in (named_heirs.split(",") if isinstance(named_heirs, str) else (named_heirs or [])) if str(h).strip()]
    if heirs and kind != "will":
        raise ClientFileError("Only a will names who inherits.")
    return {"person": person, "kind": kind, "signed_on": signed_on, "held_by": (held_by or "").strip() or None, "document": _document(source_document),
            "named": heirs or None}


async def record_estate_document(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> EstateDocument:
    ready = await prepare_estate_document(session, person_id, firm_id=firm_id, **terms)
    row = EstateDocument(firm_id=firm_id, person_id=person_id, kind=ready["kind"], signed_on=ready["signed_on"], held_by=ready["held_by"], source_document=ready["document"], recorded_by=by,
                         named=ready["named"])
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
