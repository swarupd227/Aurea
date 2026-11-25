"""Accredited investor, as a status a person recorded from the certificate (L100 India edition §2.2 and §4.2, B-291).

SEBI's accredited-investor framework exempts an investor from the SIF minimum and from the Investment Adviser fee caps (the caps
reach an individual or HUF who is not accredited; accredited and non-individual clients negotiate freely). The test itself is an
income or net-worth test the L100 edition says to confirm before use with a client, so this module never applies it: it never reads
a household's wealth or income to say someone qualifies. A person records the certificate (its reference, the agency that issued
it, the day it starts, the day it runs out, and the criteria it states it rests on, as lines of text) and that is the whole fact.

Added and never edited. The latest row for a person stands: a later certificate replaces an earlier one, and a withdrawal (with its
reason) takes the status away. A certificate past its end date reads **lapsed**, which is "not accredited"; none on file reads
"no certificate recorded", which does not mean the person is not accredited. The criteria the law states sit beside the record as
a laid-out figure (`in_accredited_criteria`) with the counsel tracker's status, and are never compared with the client.

Effects, each read from here and nowhere else: `fee_cap` leaves an accredited person's own mandates outside the cap and says so,
and `product_gates` waives the SIF minimum. A mandate held by an HUF is not an individual and is not exempted by a member's
certificate. Nothing here changes a mandate, a fee or a holding. Separate from the shelf's `InvestorCertification`, which is the
US-style class gate; neither reads the other.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.security import UserRole
from app.core.db import utcnow
from app.models.graph import Person
from app.models.india_gates import AccreditedInvestor
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
CRITERIA_KEY = "in_accredited_criteria"
CAVEAT = "Confirm the exact criteria before using them with a client; a certificate is recorded from its document and never worked out from wealth."
STATES = {"accredited": "Accredited", "lapsed": "Certificate lapsed: not accredited", "not_yet": "Certificate not yet in force: not accredited",
          "withdrawn": "Withdrawn: not accredited", "none": "No certificate recorded"}


class AccreditedError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _day(value, label: str, *, required: bool = True) -> date | None:
    if value in (None, ""):
        if required:
            raise AccreditedError(f"Give {label} as YYYY-MM-DD.")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise AccreditedError(f"Give {label} as YYYY-MM-DD.")


def state_of(row: AccreditedInvestor | None, today: date) -> str:
    if row is None:
        return "none"
    if row.state == "withdrawn":
        return "withdrawn"
    if row.valid_from > today:
        return "not_yet"
    if row.valid_to is not None and row.valid_to < today:
        return "lapsed"
    return "accredited"


def view_of(row: AccreditedInvestor | None, today: date, *, name: str | None = None) -> dict:
    state = state_of(row, today)
    out = {"state": state, "label": STATES[state], "accredited": state == "accredited", "person": name}
    if row is not None:
        out.update({"certificate_ref": row.certificate_ref, "agency": row.agency, "valid_from": row.valid_from.isoformat(),
                    "valid_to": row.valid_to.isoformat() if row.valid_to else None, "criteria": list(row.criteria or []),
                    "document": row.source_document, "recorded_by": row.recorded_by, "reason": row.reason})
    return out


async def standing(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, AccreditedInvestor]:
    """The latest row for each person."""
    latest: dict[uuid.UUID, AccreditedInvestor] = {}
    for r in (await session.execute(select(AccreditedInvestor).where(AccreditedInvestor.firm_id == firm_id)
                                    .order_by(AccreditedInvestor.created_at, AccreditedInvestor.id))).scalars().all():
        latest[r.person_id] = r
    return latest


async def accredited_persons(session: AsyncSession, firm: Firm, today: date | None = None) -> set[uuid.UUID]:
    """The people whose certificate stands today. Empty for a firm outside India."""
    if not applies(firm):
        return set()
    day = today or firm_time.today(firm)
    return {pid for pid, r in (await standing(session, firm.id)).items() if state_of(r, day) == "accredited"}


def _lines(criteria) -> list[str]:
    if isinstance(criteria, str):
        criteria = [criteria]
    return [" ".join(str(c).split()) for c in (criteria or []) if str(c).strip()]


async def prepare(session: AsyncSession, firm: Firm, role, *, person_id, state: str | None = "granted", certificate_ref: str | None = None,
                  agency: str | None = None, valid_from=None, valid_to=None, criteria=None, document: str | None = None,
                  reason: str | None = None) -> dict:
    if not applies(firm):
        raise AccreditedError("An accredited-investor certificate is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise AccreditedError("An adviser, paraplanner, operations or compliance records a certificate, from the document.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise AccreditedError("Give the person from the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise AccreditedError("Person not found.")
    kind = (state or "granted").strip().lower()
    if kind not in ("granted", "withdrawn"):
        raise AccreditedError("The state is granted, or withdrawn.")
    today = firm_time.today(firm)
    if len((document or "").strip()) < 5:
        raise AccreditedError("Say where the certificate or the withdrawal notice is filed.")
    before = (await standing(session, firm.id)).get(pid)
    if kind == "withdrawn":
        if before is None or before.state == "withdrawn":
            raise AccreditedError("There is no certificate on file to withdraw.")
        if len((reason or "").strip()) < 5:
            raise AccreditedError("Say why it is withdrawn, from the notice.")
        start, end = before.valid_from, before.valid_to
        ref, who, lines = before.certificate_ref, before.agency, list(before.criteria or [])
    else:
        start = _day(valid_from, "the day the certificate starts")
        end = _day(valid_to, "the day the certificate runs out", required=False)
        if start > today:
            raise AccreditedError("A certificate cannot be recorded before it starts: record it once it is in force.")
        if end is not None and end <= start:
            raise AccreditedError("A certificate cannot run out before it starts.")
        ref, who = (certificate_ref or "").strip() or None, (agency or "").strip() or None
        if ref is None:
            raise AccreditedError("Give the certificate's reference as it is printed.")
        if who is None:
            raise AccreditedError("Name the accreditation agency that issued it.")
        lines = _lines(criteria)
        if not lines:
            raise AccreditedError("Give the criteria the certificate says it rests on, as it states them: they are laid out, never checked against wealth.")
        if before is not None and before.state == "granted" and before.certificate_ref == ref and before.valid_from == start and before.valid_to == end:
            raise AccreditedError("That certificate is already recorded.")
    return {"person": person, "state": kind, "certificate_ref": ref, "agency": who, "valid_from": start, "valid_to": end, "criteria": lines,
            "document": document.strip(), "reason": (reason or "").strip() or None, "before": before, "today": today,
            "before_state": state_of(before, today)}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = AccreditedInvestor(created_at=utcnow(), firm_id=firm.id, person_id=p["person"].id, state=p["state"], certificate_ref=p["certificate_ref"], agency=p["agency"],
                             valid_from=p["valid_from"], valid_to=p["valid_to"], criteria=p["criteria"], source_document=p["document"],
                             reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="accredited_investor_recorded", actor=by, content={
        "person_id": str(p["person"].id), "state": row.state, "certificate_ref": row.certificate_ref, "agency": row.agency,
        "valid_from": row.valid_from.isoformat(), "valid_to": row.valid_to.isoformat() if row.valid_to else None, "document": row.source_document})
    return await overview(session, firm)


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "people": [], "counts": {}}
    rows = await standing(session, firm.id)
    names = {p.id: p for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.id.in_(list(rows) or [uuid.uuid4()])))).scalars().all()}
    people = [{**view_of(r, today, name=names[pid].full_name if pid in names else None), "person_id": str(pid),
               "household_id": str(names[pid].household_id) if pid in names and names[pid].household_id else None} for pid, r in rows.items()]
    people.sort(key=lambda p: (not p["accredited"], p["person"] or ""))
    counts = {s: sum(1 for p in people if p["state"] == s) for s in STATES if s != "none"}
    return {"applies": True, "as_of": today.isoformat(), "people": people, "counts": counts,
            "criteria": law_figures.cited([CRITERIA_KEY], today)[0], "caveat": CAVEAT,
            "effects": ["The Investment Adviser fee caps do not reach an accredited individual's own mandates.", "The SIF minimum is waived."],
            "note": "No certificate recorded does not mean a person is not accredited; a certificate is never worked out from wealth."}


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    today = firm_time.today(firm)
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == household_id).order_by(Person.full_name))).scalars().all()
    rows = await standing(session, firm.id) if applies(firm) else {}
    return {"applies": applies(firm), "as_of": today.isoformat(),
            "people": [{**view_of(rows.get(p.id), today, name=p.full_name), "person_id": str(p.id)} for p in people], "caveat": CAVEAT}
