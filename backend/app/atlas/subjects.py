"""What a manually started run is pointed at, resolved inside the caller's firm.

Shared by the Cockpit's run stream and the agents API so neither looks a client up by id alone:
an id from another firm is the same as an id that does not exist.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.atlas.base import Subject
from app.models.enums import MandateType
from app.models.graph import Household, Mandate, Person
from app.models.tenant import Firm

_MODELS = {"mandate": Mandate, "household": Household, "person": Person}


class SubjectNotFound(LookupError):
    pass


async def resolve_subject(
    session: AsyncSession, firm: Firm, subject_type: str | None, subject_id: uuid.UUID | None,
) -> tuple[Subject, MandateType | None]:
    if subject_type in (None, "firm"):
        return Subject("firm", firm.id, firm.name), None
    subject = Subject(type=subject_type, id=subject_id)
    model = _MODELS.get(subject_type)
    if model is None or subject_id is None:
        return subject, None
    row = await session.get(model, subject_id)
    if row is None or row.firm_id != firm.id:
        raise SubjectNotFound(f"No {subject_type} with that id in this firm")
    if isinstance(row, Mandate):
        subject.label = row.name
        return subject, MandateType(row.mandate_type)
    subject.label = row.full_name if isinstance(row, Person) else row.name
    return subject, None
