"""The KYC registry's status on the demonstration firm (B-386): answers recorded as a person would from the registry's response, an
on-hold client with reasons, steps and contact attempts, and two prospects, one on hold and one nobody has looked up.

Every answer is invented for the demonstration firm and says so in its evidence. Dates are days before the firm's today. Run from
`seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before; it adds nothing where one of its
own answers is already on file. Nobody else on the book is looked up, so the book view shows who is not.

  Nandini Kapoor   On hold 38 days: mobile and email not verified; one step asked, two attempts to reach her
  Subir Banerjee   Registered: documents not verified at source
  Rajesh and Meera Sharma, Karthik Iyer   Validated, with the documents the registry holds
  Farah Qureshi    A prospect on hold: PAN not linked to Aadhaar
  Devendra Rao     A prospect nobody has looked up: no timeline is promised
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, kyc_registry as k, onboarding_start
from app.core.security import UserRole
from app.models.graph import Person
from app.models.identity import User
from app.models.kyc_registry import KycLookup
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India KYC registry (demonstration)"
DOCUMENT = f"KRA status response ({SOURCE})"
ON_HOLD_PROSPECT, NOT_LOOKED_UP_PROSPECT = "Farah Qureshi", "Devendra Rao"
HELD = ["identity_proof", "address_proof", "pan_card", "photograph"]


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(KycLookup.id).where(KycLookup.firm_id == firm.id, KycLookup.recorded_by == BY).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    n = 0

    async def answer(days_ago: int, status: str, **terms) -> dict:
        nonlocal n
        n += 1
        return await k.record_lookup(s, firm, UserRole.OPERATIONS, by=BY, registry="kra", agency="CVL KRA", status=status,
                                     answered_on=today - timedelta(days=days_ago), document=DOCUMENT, **terms)

    for name in ("Rajesh Sharma", "Meera Sharma", "Karthik Iyer"):
        if name in people:
            await answer(12, "validated", person_id=people[name].id, holds=HELD)
    if "Subir Banerjee" in people:
        await answer(45, "registered", person_id=people["Subir Banerjee"].id)
    nandini = people.get("Nandini Kapoor")
    if nandini is not None:
        out = await answer(38, "on_hold", person_id=nandini.id, deficiencies=["mobile_not_verified", "email_not_verified"])
        lookup = out["recorded"]["lookup_id"]
        await k.record_step(s, firm, UserRole.OPERATIONS, by=BY, lookup_id=lookup, step="mobile_not_verified", state="asked",
                            happened_on=today - timedelta(days=33), note=SOURCE)
        await k.record_contact(s, firm, UserRole.ADVISER, by=BY, person_id=nandini.id, channel="phone", outcome="no_answer",
                               happened_on=today - timedelta(days=35))
        await k.record_contact(s, firm, UserRole.ADVISER, by=BY, person_id=nandini.id, channel="email", outcome="replied",
                               happened_on=today - timedelta(days=33))
        n += 3

    adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER, User.is_active.is_(True))
                               .order_by(User.email))).scalars().first()
    for name in (ON_HOLD_PROSPECT, NOT_LOOKED_UP_PROSPECT):
        case = (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id, OnboardingCase.prospect_name == name))).scalars().first()
        if case is None:
            if adviser is None:
                continue
            case = await onboarding_start.start(s, firm, actor=adviser, prospect_name=name, registration_type="individual")
            n += 1
        if name == ON_HOLD_PROSPECT:
            await answer(6, "on_hold", case_id=case.id, deficiencies=["pan_aadhaar_not_linked"])
    return n
