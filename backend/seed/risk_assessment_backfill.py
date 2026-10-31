"""Move questionnaires saved on a mandate into the risk assessment record, and score them.

    python -m seed.risk_assessment_backfill

Answers used to be written over `Mandate.suitability["questionnaire"]` and never scored. A household's
latest answers now live in `risk_assessment`, so these are carried across with the date they were given.

Idempotent: a household that already has an assessment is left alone, and what is on the mandate stays
there. Only the questions asked and the options offered are kept, so a partial or unrecognised form is
recorded as not scored rather than guessed at. Every firm.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import risk_profile
from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.graph import LegalEntity, Mandate, Person
from app.models.risk_assessment import RiskAssessment

log = get_logger("aurea.seed.risk_assessment_backfill")


def _when(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return utcnow()


async def apply(session: AsyncSession) -> int:
    """Create what is absent. Returns the number of assessments created."""
    done = set((await session.execute(select(RiskAssessment.household_id))).scalars().all())
    created = 0
    for m in (await session.execute(select(Mandate))).scalars().all():
        answers = (m.suitability or {}).get("questionnaire")
        if not answers:
            continue
        owner = (await session.get(Person, m.person_id) if m.person_id
                 else await session.get(LegalEntity, m.entity_id) if m.entity_id else None)
        if owner is None or owner.household_id is None or owner.household_id in done:
            continue
        scored = risk_profile.score(answers)
        session.add(RiskAssessment(
            firm_id=m.firm_id, household_id=owner.household_id, answers=risk_profile.clean(answers),
            scoring_version=risk_profile.SCORING_VERSION, tolerance=scored.get("tolerance"),
            capacity=scored.get("capacity"), indicated=scored.get("indicated"), completed_by="unknown",
            completed_by_role="unknown", completed_at=_when(m.suitability.get("questionnaire_completed_at")),
        ))
        done.add(owner.household_id)
        created += 1
        log.info("risk_assessment_backfilled", mandate=m.name, status=scored["status"])
    await session.flush()
    return created


async def backfill() -> None:
    async with SessionLocal() as s:
        created = await apply(s)
        await s.commit()
        print(f"\nCarried across {created} questionnaire(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
