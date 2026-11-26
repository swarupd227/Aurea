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
from app.core.security import UserRole
from app.models.graph import Mandate, Person
from app.models.india_polish import MajorityConversion
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


async def conversion_of(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID) -> MajorityConversion | None:
    """The record a person made that this account was converted to the adult's own, the latest, or None."""
    return (await session.execute(select(MajorityConversion).where(
        MajorityConversion.firm_id == firm_id, MajorityConversion.person_id == person_id
    ).order_by(MajorityConversion.created_at.desc()))).scalars().first()


def status_of(person: Person, guardian: GuardianshipRecord | None, today: date, conversion: MajorityConversion | None = None) -> dict:
    """Whether the person is a minor, from the date of birth on file; None where there is none. A conversion a person recorded
    ends the guardian's part: until then an adult with a guardian on file is "attained_majority" and the account waits."""
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
            "state": ("guardian_on_file" if guardian else "no_guardian") if minor
            else "converted" if conversion is not None else ("attained_majority" if guardian else "adult"),
            "converted_on": conversion.converted_on.isoformat() if conversion is not None and not minor else None}


def _view(person: Person, guardian: GuardianshipRecord | None, today: date, conversion: MajorityConversion | None = None) -> dict:
    s = status_of(person, guardian, today, conversion)
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
        v = _view(p, g, today, await conversion_of(session, firm.id, p.id))
        if v["minor"] or g is not None:
            out.append(v)
    return {"applies": True, "as_of": today.isoformat(), "minors": out,
            "no_guardian": [m["name"] for m in out if m["state"] == "no_guardian"],
            "attained_majority": [m["name"] for m in out if m["state"] == "attained_majority"],
            "converted": [m["name"] for m in out if m["state"] == "converted"],
            "note": "A minor never trades; the guardian operates the account. At majority the guardian's authority ends and the account "
                    "waits until a person records its conversion from the document; nothing here converts it."}


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
    s = status_of(person, g, today, await conversion_of(session, firm.id, person.id))
    if s["state"] == "attained_majority":
        return (f"{person.full_name} reached {s['majority_age']} on {s['majority_on']}, so the guardian's authority has ended. Record the "
                f"conversion of the account from the document before an order is placed ({CITATION}).")
    if s["minor"] and g is None:
        return (f"{person.full_name} is a minor, and an account for one is run by a guardian. Record the guardian from the "
                f"document before an order is placed ({CITATION}).")
    if s["minor"]:
        return await _minor_papers(session, firm, person, g)
    return None


async def _minor_papers(session: AsyncSession, firm: Firm, minor: Person, g: GuardianshipRecord) -> str | None:
    """What a minor's account needs beyond a named guardian (B-283): the guardian's own KYC record, which needs the guardian to be a person on
    file, and the minor's PAN, recorded from a document with the guardian linked. Each missing paper is named; none is worked out."""
    from app.aurea_core import kyc_india

    lacking: list[str] = []
    if g.guardian_person_id is None:
        lacking.append(f"{g.guardian_name} is recorded by name alone, so has no KYC record: record the guardian as a person, linked to the minor")
    elif ("person", g.guardian_person_id) not in await kyc_india.latest(session, firm.id):
        lacking.append(f"the guardian {g.guardian_name} has no KYC record of their own")
    if not minor.pan:
        lacking.append(f"no PAN is recorded for {minor.full_name} (record it from the document, with the guardian linked)")
    if not lacking:
        return None
    return (f"{minor.full_name} is a minor, and an order on the account waits for the guardian's papers: {'; '.join(lacking)} "
            f"(record_kyc, record_pan) ({CITATION}).")


async def papers(session: AsyncSession, firm: Firm) -> dict:
    """Each minor's guardian KYC and PAN on file, for the Licences page and the conversation: what is there, and what a person still records."""
    from app.aurea_core import kyc_india, pan as pan_mod

    base = await overview(session, firm)
    if not base.get("applies"):
        return {"applies": False, "minors": []}
    latest = await kyc_india.latest(session, firm.id)
    out = []
    for m in base["minors"]:
        if not m["minor"]:
            continue
        g = m["guardian"]
        person = await session.get(Person, uuid.UUID(m["person_id"]))
        gid = uuid.UUID(g["person_id"]) if g and g.get("person_id") else None
        rec = latest.get(("person", gid)) if gid else None
        out.append({"person_id": m["person_id"], "name": m["name"], "age": m["age"], "guardian": g["name"] if g else None,
                    "guardian_linked": gid is not None, "guardian_kyc": ("on_file" if rec is not None else "not_on_file") if gid else "no_person",
                    "guardian_kyc_on": rec.kyc_completed_on.isoformat() if rec is not None and rec.kyc_completed_on else None,
                    "pan": pan_mod.mask(person.pan) if person is not None else None,
                    "ready": bool(g and gid is not None and rec is not None and person is not None and person.pan)})
    return {"applies": True, "as_of": base["as_of"], "minors": out, "waiting": [r["name"] for r in out if not r["ready"]],
            "note": "A minor's account trades only with the guardian's own KYC record and the minor's PAN on file. Not on file does not mean not done."}


# ── A minor's account converted at majority (B-262) ─────────────────────────────────────────────────────────────

CONVERTERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})


async def prepare_conversion(session: AsyncSession, firm: Firm, *, person_id, document: str | None, converted_on=None) -> dict:
    """What recording a conversion would write. Only for a person who has reached the age of majority with a guardian on file."""
    if not _applies(firm):
        raise GuardianError("A conversion is recorded for an Indian firm.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise GuardianError("Give the person's id, from the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise GuardianError("That person is not a client of this firm.")
    today = firm_time.today(firm)
    g = await standing(session, firm.id, pid)
    before = await conversion_of(session, firm.id, pid)
    s = status_of(person, g, today, before)
    if s["state"] == "converted":
        raise GuardianError(f"{person.full_name}'s account was already converted on {s['converted_on']}.")
    if s["state"] != "attained_majority":
        raise GuardianError(f"{person.full_name} has no minor's account to convert here ({s['state'].replace('_', ' ')}): a conversion follows "
                            "a guardian on file and the age of majority.")
    if len((document or "").strip()) < 4:
        raise GuardianError("Say where it is on file: the signed conversion form, the adult's KYC and the end of the guardian's authority.")
    day = today
    if converted_on not in (None, ""):
        try:
            day = converted_on if isinstance(converted_on, date) else date.fromisoformat(str(converted_on)[:10])
        except ValueError:
            raise GuardianError("Write the day of conversion as YYYY-MM-DD.")
        if day > today:
            raise GuardianError("A conversion cannot be dated in the future.")
        if day < date.fromisoformat(s["majority_on"]):
            raise GuardianError(f"A conversion cannot be dated before {person.full_name} reached majority on {s['majority_on']}.")
    return {"person": person, "guardian": g, "converted_on": day, "document": document.strip(), "status": s}


async def record_conversion(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare_conversion(session, firm, **terms)
    row = MajorityConversion(firm_id=firm.id, person_id=p["person"].id, converted_on=p["converted_on"], document=p["document"], recorded_by=by,
                             note=(note or "").strip() or None, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="majority_conversion_recorded", actor=by, content={
        "person_id": str(p["person"].id), "converted_on": p["converted_on"].isoformat(), "document": p["document"]})
    return await overview(session, firm)
