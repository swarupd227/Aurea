"""Top up an existing demo database with the richer onboarding cases.

`seed.run` short-circuits when the demo firm already exists, so a database seeded before
these cases were added will never receive them. This adds only what is missing.

    python -m seed.onboarding_refresh

Safe to run repeatedly — `_acquire_onboard` skips any case whose prospect name is already
present, and the acquired-book batch is guarded too.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.onboarding import BeneficialOwner, OnboardingCase
from app.models.tenant import Firm

from seed.run import _acquire_onboard

log = get_logger("aurea.seed.onboarding_refresh")


async def refresh() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if not firm:
            log.error("refresh_no_firm", msg="Demo firm not found — run seed.run first")
            return

        before = len((await s.execute(
            select(OnboardingCase.id).where(OnboardingCase.firm_id == firm.id)
        )).scalars().all())

        await _acquire_onboard(s, firm)
        await s.commit()

        cases = (await s.execute(
            select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
        )).scalars().all()
        bo_count = len((await s.execute(
            select(BeneficialOwner.id).where(BeneficialOwner.firm_id == firm.id)
        )).scalars().all())

        log.info("refresh_done", added=len(cases) - before, total=len(cases),
                 beneficial_owners=bo_count)

        print(f"\nOnboarding cases: {before} -> {len(cases)}  (+{len(cases) - before})")
        print(f"Beneficial owners: {bo_count}\n")
        print(f"{'prospect':34}{'registration':20}{'tier':8}{'status':10}")
        print("-" * 72)
        for c in sorted(cases, key=lambda x: x.prospect_name):
            print(f"{c.prospect_name:34}{str(c.registration_type or '—'):20}"
                  f"{str(c.aml_risk_tier or '—'):8}{str(c.status):10}")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(refresh())
