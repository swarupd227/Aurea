"""Who is a fund manager's own employee or director, as a person recorded it from a document (B-303).

An AIF's minimum is lower for the manager's own employees and directors (`product_gates`, the `in_aif_min_ticket_employee` figure).
Who works for a manager is not something the platform can know: a person records it, with the manager's name and the document (an
appointment letter, a register of directors), and it stands until a person records that it ended. Added and never edited; the latest
for a person and a manager stands. The lower minimum is applied only to a product whose name or `manager` tag names that manager; a
person recorded for another manager is shown, and the standard minimum applies. Nothing here is worked out from a profession, a
surname or a holding.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Person
from app.models.india_rem_d import ManagerAffiliation
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
ROLES = {"employee": "Employee", "director": "Director"}
STATES = {"in_force": "In force", "ended": "Ended"}


class StaffError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _key(name: str) -> str:
    return " ".join(name.lower().split())


async def standing(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID | None = None) -> dict[tuple, ManagerAffiliation]:
    """The latest row for each (person, manager)."""
    q = select(ManagerAffiliation).where(ManagerAffiliation.firm_id == firm_id)
    if person_id is not None:
        q = q.where(ManagerAffiliation.person_id == person_id)
    out: dict[tuple, ManagerAffiliation] = {}
    for r in (await session.execute(q.order_by(ManagerAffiliation.created_at, ManagerAffiliation.id))).scalars().all():
        out[(r.person_id, _key(r.manager))] = r
    return out


def matches(row: ManagerAffiliation, *, product_name: str | None, product_manager: str | None) -> bool:
    """Whether this affiliation is with the manager of the product: its `manager` tag, else its name carrying the manager's name."""
    want = _key(row.manager)
    if product_manager:
        return _key(product_manager) == want
    return bool(product_name) and want in _key(product_name)


async def for_person(session: AsyncSession, firm: Firm, person_id: uuid.UUID | None, today: date | None = None) -> list[ManagerAffiliation]:
    """The affiliations in force for a person today. Empty for a firm outside India or a person with none recorded."""
    if person_id is None or not applies(firm):
        return []
    day = today or firm_time.today(firm)
    return [r for r in (await standing(session, firm.id, person_id)).values() if r.state == "in_force" and r.effective_on <= day]


async def prepare(session: AsyncSession, firm: Firm, role, *, person_id, manager: str | None, affiliation: str | None = "employee",
                  state: str | None = "in_force", effective_on=None, document: str | None = None) -> dict:
    if not applies(firm):
        raise StaffError("A manager's staff are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise StaffError("An adviser, paraplanner, operations or compliance records who works for a manager, from the document.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise StaffError("Give the person from the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise StaffError("Person not found.")
    name = " ".join((manager or "").split())
    if len(name) < 3:
        raise StaffError("Name the fund manager, as the document gives it.")
    kind = (affiliation or "employee").strip().lower()
    if kind not in ROLES:
        raise StaffError("The role is employee or director.")
    st = (state or "in_force").strip().lower()
    if st not in STATES:
        raise StaffError("The state is in_force, or ended.")
    if len((document or "").strip()) < 5:
        raise StaffError("Say where the appointment letter or the register of directors is filed.")
    today = firm_time.today(firm)
    day = today
    if effective_on not in (None, ""):
        try:
            day = effective_on if isinstance(effective_on, date) else date.fromisoformat(str(effective_on)[:10])
        except ValueError:
            raise StaffError("Write the day it takes effect as YYYY-MM-DD.")
    if day > today:
        raise StaffError("It cannot take effect in the future: record it when it is in force.")
    before = (await standing(session, firm.id, pid)).get((pid, _key(name)))
    if st == "ended" and (before is None or before.state == "ended"):
        raise StaffError(f"There is no recorded post at {name} in force to end.")
    if st == "in_force" and before is not None and before.state == "in_force" and before.role == kind:
        raise StaffError(f"{person.full_name} is already recorded as {name}'s {kind}.")
    return {"person": person, "manager": name, "role": kind, "state": st, "effective_on": day, "document": document.strip(), "before": before}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = ManagerAffiliation(created_at=utcnow(), firm_id=firm.id, person_id=p["person"].id, manager=p["manager"], role=p["role"], state=p["state"],
                             effective_on=p["effective_on"], document=p["document"], recorded_by=by, note=(note or "").strip() or None)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="manager_affiliation_recorded", actor=by, content={
        "person_id": str(p["person"].id), "manager": row.manager, "role": row.role, "state": row.state, "document": row.document})
    return await overview(session, firm)


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "people": []}
    rows = await standing(session, firm.id)
    names = {p.id: p for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.id.in_([k[0] for k in rows] or [uuid.uuid4()])))).scalars().all()}
    people = [{"person_id": str(r.person_id), "person": names[r.person_id].full_name if r.person_id in names else None, "manager": r.manager,
               "role": r.role, "role_label": ROLES[r.role], "state": r.state, "state_label": STATES[r.state], "effective_on": r.effective_on.isoformat(),
               "document": r.document, "recorded_by": r.recorded_by} for r in rows.values()]
    people.sort(key=lambda p: (p["state"] != "in_force", p["person"] or "", p["manager"]))
    return {"applies": True, "as_of": today.isoformat(), "people": people,
            "note": "Recorded from a document. The lower AIF minimum applies only to a product of that manager; nobody is worked out to be staff."}
