"""A minor's account is run by a guardian a person recorded from a document (India row 7, B-257).

A minor cannot contract, so an Indian account for one is operated by a natural or court-appointed guardian, and the
minor never trades. Whether someone is a minor is worked from their recorded date of birth and the Indian Majority Act's
age (18, or 21 where a court appointed a guardian); with no date of birth that is "not assessed", never a guess. A
guardian is added and never edited, and the latest stands. An order on a mandate a minor holds is refused until a
guardian is on file, and once the minor reaches majority the account is flagged for conversion: nothing here converts it
or moves any money. Not on file does not mean none, and who may act for a minor under personal law is for counsel.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.models.graph import Mandate, Person
from app.models.residency import GuardianshipRecord
from app.models.tenant import Firm
from app.provenance import ledger

RELATIONSHIPS = {"father": "Father", "mother": "Mother", "legal_guardian": "Legal guardian",
                 "court_appointed": "Guardian appointed by a court"}
CITATION = "Indian Majority Act 1875 s.3; the guardian's authority is personal law, for counsel"


class GuardianError(ValueError):
    """This cannot be recorded, with the reason."""


def _applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def age_on(born: date, day: date) -> int:
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


async def standing(session: AsyncSession, firm_id: uuid.UUID, minor_id: uuid.UUID) -> GuardianshipRecord | None:
    return (await session.execute(select(GuardianshipRecord).where(
        GuardianshipRecord.firm_id == firm_id, GuardianshipRecord.minor_person_id == minor_id
    ).order_by(GuardianshipRecord.created_at.desc()))).scalars().first()


def majority_age(guardian: GuardianshipRecord | None, today: date) -> int:
    court = guardian is not None and guardian.relationship == "court_appointed"
    return int(law_figures.value("in_majority_age_court_guardian" if court else "in_majority_age", today))


def status_of(person: Person, guardian: GuardianshipRecord | None, today: date) -> dict:
    """Whether the person is a minor, from the date of birth on file; None where there is none."""
    if person.date_of_birth is None:
        return {"minor": None, "age": None, "majority_on": None, "state": "not_assessed",
                "message": "No date of birth on file, so whether this client is a minor is not assessed."}
    age = age_on(person.date_of_birth, today)
    limit = majority_age(guardian, today)
    b = person.date_of_birth
    try:
        majority_on = date(b.year + limit, b.month, b.day)
    except ValueError:      # born on 29 February
        majority_on = date(b.year + limit, 3, 1)
    minor = age < limit
    return {"minor": minor, "age": age, "majority_age": limit, "majority_on": majority_on.isoformat(),
            "state": ("guardian_on_file" if guardian else "no_guardian") if minor else ("attained_majority" if guardian else "adult")}


def _view(person: Person, guardian: GuardianshipRecord | None, today: date) -> dict:
    s = status_of(person, guardian, today)
    return {"person_id": str(person.id), "name": person.full_name, **s,
            "guardian": ({"name": guardian.guardian_name, "person_id": str(guardian.guardian_person_id) if guardian.guardian_person_id else None,
                          "relationship": guardian.relationship, "relationship_label": RELATIONSHIPS[guardian.relationship],
                          "document": guardian.document, "recorded_by": guardian.recorded_by} if guardian else None),
            "citation": CITATION}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every client who is a minor, or who was one with a guardian on file, and where each stands."""
    if not _applies(firm):
        return {"applies": False, "minors": []}
    today = firm_time.today(firm)
    out = []
    for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.deceased_on.is_(None),
                                                         Person.date_of_birth.is_not(None)).order_by(Person.full_name))).scalars().all():
        g = await standing(session, firm.id, p.id)
        v = _view(p, g, today)
        if v["minor"] or g is not None:
            out.append(v)
    return {"applies": True, "as_of": today.isoformat(), "minors": out,
            "no_guardian": [m["name"] for m in out if m["state"] == "no_guardian"],
            "attained_majority": [m["name"] for m in out if m["state"] == "attained_majority"],
            "note": "A minor never trades; the guardian operates the account. Nothing here converts an account at majority."}


async def prepare(session: AsyncSession, firm: Firm, *, minor_person_id, guardian_name: str | None, relationship: str | None,
                  document: str | None, guardian_person_id=None) -> dict:
    if not _applies(firm):
        raise GuardianError("A guardian is recorded for an Indian firm.")
    try:
        mid = minor_person_id if isinstance(minor_person_id, uuid.UUID) else uuid.UUID(str(minor_person_id))
    except ValueError:
        raise GuardianError("Give the minor's id, from the client's page.")
    minor = await session.get(Person, mid)
    if minor is None or minor.firm_id != firm.id:
        raise GuardianError("That person is not a client of this firm.")
    today = firm_time.today(firm)
    if minor.date_of_birth is None:
        raise GuardianError(f"{minor.full_name} has no date of birth on file; record it from a document before naming a guardian.")
    rel = (relationship or "").strip().lower()
    if rel not in RELATIONSHIPS:
        raise GuardianError(f"A relationship is one of {', '.join(RELATIONSHIPS)}.")
    name = " ".join((guardian_name or "").split())
    guardian = None
    if guardian_person_id:
        try:
            guardian = await session.get(Person, uuid.UUID(str(guardian_person_id)))
        except ValueError:
            raise GuardianError("Give the guardian's id, or leave it out when the guardian is not a client.")
        if guardian is None or guardian.firm_id != firm.id:
            raise GuardianError("That guardian is not a client of this firm; give the name alone.")
        name = name or guardian.full_name
    if len(name) < 3:
        raise GuardianError("Name the guardian.")
    if guardian is not None and guardian.id == minor.id:
        raise GuardianError("A person cannot be their own guardian.")
    if guardian is not None and guardian.date_of_birth is not None and age_on(guardian.date_of_birth, today) < 18:
        raise GuardianError(f"{guardian.full_name} is under 18 and cannot be a guardian.")
    if len((document or "").strip()) < 4:
        raise GuardianError("Say where it is on file: the birth certificate or the court order that names the guardian.")
    before = await standing(session, firm.id, mid)
    if before is not None and before.guardian_name == name and before.relationship == rel:
        raise GuardianError(f"{name} is already recorded as {minor.full_name}'s guardian.")
    return {"minor": minor, "guardian_name": name, "guardian_person_id": guardian.id if guardian else None, "relationship": rel,
            "document": document.strip(), "before": before.guardian_name if before else None,
            "status": status_of(minor, GuardianshipRecord(relationship=rel), today)}


async def record(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare(session, firm, **terms)
    row = GuardianshipRecord(firm_id=firm.id, minor_person_id=p["minor"].id, guardian_person_id=p["guardian_person_id"],
                             guardian_name=p["guardian_name"], relationship=p["relationship"], document=p["document"], recorded_by=by,
                             note=(note or "").strip() or None, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="guardian_recorded", actor=by, content={
        "minor_person_id": str(p["minor"].id), "guardian": row.guardian_name, "relationship": row.relationship, "document": row.document})
    return await overview(session, firm)


async def order_refusal(session: AsyncSession, firm: Firm, mandate: Mandate) -> str | None:
    """Why an order on this mandate may not be placed, or None. A minor's account needs a guardian on file."""
    if not _applies(firm) or mandate.person_id is None:
        return None
    person = await session.get(Person, mandate.person_id)
    if person is None or person.date_of_birth is None:
        return None
    today = firm_time.today(firm)
    g = await standing(session, firm.id, person.id)
    s = status_of(person, g, today)
    if s["minor"] and g is None:
        return (f"{person.full_name} is a minor, and an account for one is run by a guardian. Record the guardian from the "
                f"document before an order is placed ({CITATION}).")
    return None
