"""India estate and NRI planning on the demonstration firm (B-398).

Every record is invented for the demonstration firm and says so. Run from `seed.india_demo` when the firm is built and from
`seed.india_demo_backfill` on a firm built before; it adds nothing where its own records are already on file.

  The Sharmas   Rajesh's will carries its two witnesses and an executor who has been told, and was checked against the
                register: it needs change, with a task. A wish for the family business is recorded.
  Banerjee      Subir's will has one witness on file and an executor nobody recorded as told; never checked.
  The Menons    Vikram's tax residency certificate from the United Arab Emirates covers this tax year; Anita has none. A
                return to India is planned, with its checklist tasks and the not-ordinarily-resident outlook.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import estate_register, firm_time, india_estate, nri_planning
from app.models.client_file import EstateDocument
from app.models.estate_nri import EstateWish, ResidenceMove, TreatyCertificate
from app.models.graph import Person

BY = "seed: india estate and NRI (demonstration)"
SOURCE = "Demonstration record, invented for the demonstration firm"
PLANNED_IN_DAYS = 150


async def add(s: AsyncSession, firm) -> int:
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    added = 0

    async def mine(model, person) -> bool:
        return (await s.execute(select(model.id).where(model.firm_id == firm.id, model.person_id == person.id,
                                                       model.recorded_by == BY).limit(1))).first() is not None

    async def will_of(person) -> bool:
        return (await s.execute(select(EstateDocument.id).where(
            EstateDocument.person_id == person.id, EstateDocument.kind == "will").limit(1))).first() is not None

    rajesh = people.get("Rajesh Sharma")
    if rajesh is not None and not await mine(EstateWish, rajesh):
        await estate_register.record_wish(s, firm, by=BY, person_id=rajesh.id, kind="business_succession",
                                          words="The components business is to pass to Aarav, with Meera provided for outside it",
                                          document=f"Meeting note with Rajesh Sharma ({SOURCE})")
        added += 1
        if await will_of(rajesh):
            await india_estate.record_will_status(s, firm, by=BY, person_id=rajesh.id, witnesses="A. Kulkarni; S. Deshpande",
                                                  executor="Meera Sharma", executor_told=True,
                                                  document=f"Registered will, attestation page, and a note of the call with the executor ({SOURCE})")
            await estate_register.record_check(s, firm, by=BY, person_id=rajesh.id, outcome="needs_change",
                                               findings="The will does not speak to the HUF interest or to the assets recorded since it was signed",
                                               document=f"Adviser's note of the yearly check ({SOURCE})")
            added += 2
    subir = people.get("Subir Banerjee")
    if subir is not None and await will_of(subir):
        status = await india_estate._standing_will(s, firm.id, subir.id)
        if status is None or not status.witnesses:
            await india_estate.record_will_status(s, firm, by=BY, person_id=subir.id, witnesses="P. Mukherjee", executor="Aniket Banerjee",
                                                  executor_told=False, document=f"The will's attestation page ({SOURCE})")
            added += 1
    vikram = people.get("Vikram Menon")
    if vikram is not None:
        if not await mine(TreatyCertificate, vikram):
            start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
            await nri_planning.record_certificate(s, firm, by=BY, person_id=vikram.id, kind="trc", country="United Arab Emirates",
                                                  reference="DEMO-TRC-2026-0417", valid_from=start,
                                                  valid_to=date(start.year + 1, 3, 31),
                                                  document=f"Certificate from the foreign tax authority ({SOURCE})")
            added += 1
        if not await mine(ResidenceMove, vikram):
            await nri_planning.record_move(s, firm, by=BY, person_id=vikram.id, direction="returning",
                                           moved_on=today + timedelta(days=PLANNED_IN_DAYS), country="United Arab Emirates",
                                           document=f"Note of the call: the family plans to return ({SOURCE})")
            added += 1
    return added
