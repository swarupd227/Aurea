"""Residence and guardian facts an onboarding case already holds, read into the records only when a person confirms (B-259).

An NRI or minor case carries a few facts in its registration detail: the country of residence (and, on an NRE or NRO registration,
which account), and a minor's guardian and relationship. Until now a person typed them again into `residency_record` or
`guardianship_record`. This reads what the case holds and lays it beside what is on file, so a Confirm card can show the exact
records that would be written. Nothing is written on a read, and nothing is worked out: a FEMA status, a day count or a tax
residency is not read from a registration type, because a registration type does not state one. A person confirms, with the
evidence, and the records are the same ones `residency.record` and `guardianship.record` write.

A case that has not yet made its client (no person materialised) has nothing to record against, and says so.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import guardianship, residency
from app.models.graph import Person
from app.models.onboarding import OnboardingCase, OnboardingDocument
from app.models.tenant import Firm

RECORDERS = residency.RECORDERS
EVIDENCE_KINDS = ("fatca_crs_self_cert", "visa_or_oci", "overseas_address_proof", "guardian_proof", "birth_certificate")
ACCOUNT_OF = {"nri_nre": "NRE", "nri_nro": "NRO"}
RELATION_WORDS = {"father": "father", "mother": "mother", "legal guardian": "legal_guardian", "legal_guardian": "legal_guardian",
                  "guardian": "legal_guardian", "court appointed": "court_appointed", "court_appointed": "court_appointed",
                  "court-appointed": "court_appointed"}


class CaseFactsError(ValueError):
    """These cannot be read or recorded, with the reason."""


def _relationship(value) -> str | None:
    return RELATION_WORDS.get(" ".join(str(value or "").lower().split()))


async def _case(session: AsyncSession, firm: Firm, case_id) -> OnboardingCase:
    try:
        cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    except ValueError:
        raise CaseFactsError("Give the onboarding case's id.")
    case = await session.get(OnboardingCase, cid)
    if case is None or case.firm_id != firm.id:
        raise CaseFactsError("That is not an onboarding case of this firm.")
    return case


async def _evidence(session: AsyncSession, case: OnboardingCase, kinds: tuple[str, ...]) -> str | None:
    docs = (await session.execute(select(OnboardingDocument).where(
        OnboardingDocument.case_id == case.id, OnboardingDocument.doc_type.in_(kinds)))).scalars().all()
    if not docs:
        return None
    return f"Onboarding case {case.prospect_name}: " + ", ".join(f"{d.filename} ({d.doc_type})" for d in docs)


async def read(session: AsyncSession, firm: Firm, case_id) -> dict:
    """What the case holds that a residence or guardian record would take, and where each stands against what is on file. Writes nothing."""
    if not residency.applies(firm):
        raise CaseFactsError("Residence and guardian facts are read for an Indian firm.")
    case = await _case(session, firm, case_id)
    detail = (case.intake or {}).get("registration") or {}
    person_id = (case.materialized or {}).get("person_id")
    person = await session.get(Person, uuid.UUID(str(person_id))) if person_id else None
    out = {"applies": True, "case_id": str(case.id), "case": case.prospect_name, "registration_type": case.registration_type,
           "person_id": str(person.id) if person else None, "person": person.full_name if person else None,
           "residence": None, "guardian": None, "unread": []}
    country = (detail.get("country_of_residence") or "").strip()
    account = ACCOUNT_OF.get(case.registration_type or "")
    if country or account:
        facts = {**({"country_of_residence": country} if country else {}), **({"accounts": [account]} if account else {})}
        out["residence"] = {"facts": facts, "evidence": await _evidence(session, case, EVIDENCE_KINDS[:3])}
    elif case.registration_type in ACCOUNT_OF:
        out["unread"].append("The case records no country of residence.")
    name = (detail.get("guardian_name") or "").strip()
    if name or case.registration_type == "minor_guardian":
        rel = _relationship(detail.get("guardian_relationship"))
        if not name:
            out["unread"].append("The case records no guardian.")
        elif rel is None:
            out["unread"].append(f"The guardian's relationship ({detail.get('guardian_relationship') or 'not recorded'}) is not one of "
                                 f"{', '.join(guardianship.RELATIONSHIPS)}, so it is not read.")
        else:
            out["guardian"] = {"guardian_name": name, "relationship": rel, "evidence": await _evidence(session, case, EVIDENCE_KINDS[3:])}
    if out["residence"] is None and out["guardian"] is None and not out["unread"]:
        out["unread"].append("The case holds no residence or guardian fact.")
    if person is None and (out["residence"] or out["guardian"]):
        out["unread"].append("The case has not made its client yet: records follow once it is materialised.")
    return out


async def prepare(session: AsyncSession, firm: Firm, *, case_id, document: str | None = None) -> dict:
    """What confirming would write, each record checked by the module that writes it. Writes nothing."""
    r = await read(session, firm, case_id)
    if r["person_id"] is None:
        raise CaseFactsError("The case has not made its client yet, so there is nobody to record these against.")
    case = await _case(session, firm, case_id)
    todo = {"case": case, "read": r, "residence": None, "guardian": None, "already": []}
    if r["residence"]:
        ev = (document or "").strip() or r["residence"]["evidence"]
        if not ev:
            raise CaseFactsError("Say where the residence evidence is filed: the case holds no passport, visa or FATCA and CRS form on record.")
        try:
            todo["residence"] = await residency.prepare(session, firm, person_id=r["person_id"], document=ev, **r["residence"]["facts"])
        except residency.ResidencyError as exc:
            if str(exc).startswith("Nothing changes"):
                todo["already"].append("The residence facts the case holds are already on file.")
            else:
                raise CaseFactsError(str(exc))
    if r["guardian"]:
        g = r["guardian"]
        ev = (document or "").strip() or g["evidence"]
        if not ev:
            raise CaseFactsError("Say where the guardian's authority is filed: the case holds no birth certificate or court order on record.")
        try:
            todo["guardian"] = await guardianship.prepare(session, firm, minor_person_id=r["person_id"], guardian_name=g["guardian_name"],
                                                          relationship=g["relationship"], document=ev)
        except guardianship.GuardianError as exc:
            if "already recorded" in str(exc):
                todo["already"].append("The guardian the case names is already on file.")
            else:
                raise CaseFactsError(str(exc))
    if todo["residence"] is None and todo["guardian"] is None:
        raise CaseFactsError(" ".join(todo["already"]) or "Nothing in the case is new to the records.")
    return todo


async def record(session: AsyncSession, firm: Firm, *, by: str, case_id, document: str | None = None) -> dict:
    p = await prepare(session, firm, case_id=case_id, document=document)
    r = p["read"]
    done = []
    if p["residence"]:
        await residency.record(session, firm, by=by, person_id=r["person_id"], document=p["residence"]["document"], **r["residence"]["facts"])
        done.append("residence")
    if p["guardian"]:
        g = r["guardian"]
        await guardianship.record(session, firm, by=by, minor_person_id=r["person_id"], guardian_name=g["guardian_name"],
                                  relationship=g["relationship"], document=p["guardian"]["document"])
        done.append("guardian")
    return {"applies": True, "case_id": r["case_id"], "case": r["case"], "person": r["person"], "recorded": done, "already": p["already"],
            "residence": await residency.person_view(session, firm, p["residence"]["person"]) if p["residence"] else None,
            "guardians": (await guardianship.overview(session, firm)) if p["guardian"] else None}
