"""PAN, the Indian tax identifier, recorded by a person from the card or a document (India row 4).

A PAN is ten characters: five letters, four digits, a letter. Its fourth letter says what holds it: P for a person,
H for an HUF, T for a trust, C for a company, F for a firm or partnership. A PAN is recorded against a person or an
entity, is unique at the firm, and is shown masked everywhere: on a card, to the model in a tool result, and in the
ledger. Only the last four characters are visible.

Not done: it is stored as written, not encrypted at rest, because nothing in the platform encrypts a field yet. And
a PAN typed into the conversation reaches the model, as everything typed there does (research report, blocker B2).
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Household, LegalEntity, Person
from app.models.tenant import Firm
from app.provenance import ledger

PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# What the fourth character says about the holder, for the holders the platform records.
HOLDER = {"person": "P", "huf": "H", "trust": "T", "company": "C", "llc": "C", "corp": "C", "partnership": "F"}
HOLDER_WORD = {"P": "a person", "H": "an HUF", "T": "a trust", "C": "a company", "F": "a firm or partnership",
               "A": "an association of persons", "B": "a body of individuals", "G": "a government", "J": "an artificial juridical person",
               "L": "a local authority"}


class PanError(Exception):
    """A PAN that cannot be recorded as given. Carries a reason fit to show the user."""


def mask(pan: str | None) -> str | None:
    return ("•" * 6 + pan[-4:]) if pan else None


def normalise(pan: str | None) -> str:
    return re.sub(r"\s+", "", (pan or "")).upper()


async def _holder(session: AsyncSession, firm: Firm, person_id: uuid.UUID | None, entity_id: uuid.UUID | None):
    if bool(person_id) == bool(entity_id):
        raise PanError("Say whose PAN it is: one person or one entity.")
    row = await session.get(Person, person_id) if person_id else await session.get(LegalEntity, entity_id)
    household = await session.get(Household, row.household_id) if row is not None and row.household_id else None
    if row is None or household is None or household.firm_id != firm.id:
        raise PanError("No such person or entity at this firm.")
    kind = "person" if person_id else str(row.entity_type)
    return row, kind


async def prepare(session: AsyncSession, firm: Firm, *, person_id: uuid.UUID | None = None, entity_id: uuid.UUID | None = None,
                  pan: str | None, source_document: str | None) -> dict:
    row, kind = await _holder(session, firm, person_id, entity_id)
    value = normalise(pan)
    if not PATTERN.match(value):
        raise PanError("A PAN is five letters, four digits and a letter, like ABCPE1234F.")
    want = HOLDER.get(kind)
    if want and value[3] != want:
        raise PanError(f"The fourth letter of this PAN, {value[3]}, is {HOLDER_WORD.get(value[3], 'not a holder type')}'s, "
                       f"and {getattr(row, 'full_name', None) or row.name} is {HOLDER_WORD[want]}.")
    named = (source_document or "").strip()
    if len(named) < 5:
        raise PanError("Say which document the PAN is read from: the PAN card, a KYC record, a return.")
    taken = (await session.execute(select(Person.id).join(Household, Household.id == Person.household_id).where(
        Household.firm_id == firm.id, Person.pan == value, Person.id != (person_id or uuid.uuid4())))).first() or \
        (await session.execute(select(LegalEntity.id).join(Household, Household.id == LegalEntity.household_id).where(
            Household.firm_id == firm.id, LegalEntity.pan == value, LegalEntity.id != (entity_id or uuid.uuid4())))).first()
    if taken:
        raise PanError("This PAN is already on file at this firm for someone else. A PAN belongs to one holder.")
    return {"holder": row, "kind": kind, "name": getattr(row, "full_name", None) or row.name, "pan": value,
            "replaces": mask(row.pan) if row.pan and row.pan != value else None, "document": named,
            "unchanged": row.pan == value}


async def record(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    ready = await prepare(session, firm, **terms)
    if ready["unchanged"]:
        raise PanError("This PAN is already on file for them.")
    ready["holder"].pan = ready["pan"]
    await ledger.append_entry(session, firm_id=firm.id, event_type="pan_recorded", actor=by, content={
        "holder": ready["name"], "kind": ready["kind"], "pan": mask(ready["pan"]), "replaces": ready["replaces"],
        "source_document": ready["document"]})
    await session.flush()
    return {"name": ready["name"], "kind": ready["kind"], "pan": mask(ready["pan"]), "replaces": ready["replaces"]}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[dict]:
    """Everyone in the household who could hold a PAN, with it masked or not on file."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return []
    people = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))).scalars().all()
    return ([{"person_id": str(p.id), "name": p.full_name, "kind": "person", "pan": mask(p.pan)} for p in people]
            + [{"entity_id": str(e.id), "name": e.name, "kind": str(e.entity_type), "pan": mask(e.pan)} for e in entities])
