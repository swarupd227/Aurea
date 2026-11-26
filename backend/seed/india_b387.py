"""Identity and bank checks on the demonstration firm (B-387), done by hand as a person would from a document, so the human queue shows.

No provider is connected in this seed and none is implied: every attempt here is the alternative method, invented for the
demonstration firm and saying so in its evidence. Run from `seed.india_demo` when the firm is built and from
`seed.india_demo_backfill` on a firm built before; it adds nothing where one of its own attempts is already on file.

  Karthik Iyer    video verification done in person: passed by hand
  Subir Banerjee  a cancelled cheque showing the full name: passed by hand
  Meera Sharma    a bank statement naming another holder: failed by hand, waiting in the operations queue
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import identity_checks as c
from app.core.security import UserRole
from app.models.graph import Person
from app.models.identity_checks import VerificationAttempt
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India identity checks (demonstration)"
BANK = "A demonstration bank (invented)"
# person, check, outcome, further terms
ATTEMPTS = (
    ("Karthik Iyer", "vipv", "passed", {"document": f"In-person verification record signed by the officer ({SOURCE})"}),
    ("Subir Banerjee", "bank", "passed", {"bank": BANK, "last4": "6642", "name_on_document": "Subir Banerjee",
                                          "document": f"Cancelled cheque ({SOURCE})"}),
    ("Meera Sharma", "bank", "failed", {"bank": BANK, "last4": "2087", "name_on_document": "M K Verma",
                                        "document": f"Bank statement naming another holder ({SOURCE})"}),
)


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(VerificationAttempt.id).where(VerificationAttempt.firm_id == firm.id,
                                                             VerificationAttempt.recorded_by == BY).limit(1))).first():
        return 0
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    n = 0
    for name, kind, outcome, terms in ATTEMPTS:
        if name not in people:
            continue
        try:
            await c.record_result(s, firm, UserRole.OPERATIONS, by=BY, kind=kind, person_id=people[name].id, outcome=outcome, **terms)
        except c.CheckError:
            continue          # what a person has since recorded on the deployed firm stands
        n += 1
    return n
