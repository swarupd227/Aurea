"""Backfill OnboardingParty from data that predates the party model.

Before step 2 the only modelled party was BeneficialOwner, and everyone else — the
applicant, joint owners, trustees, POA holders — existed only as free-text names inside
`intake`. This promotes all of them to real party records so the completeness gate has
something to check.

    python -m seed.party_backfill

Idempotent: parties are matched on (case, role, legal_name).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.enums import PartyRole
from app.models.onboarding import BeneficialOwner, OnboardingCase, OnboardingParty
from app.models.tenant import Firm

log = get_logger("aurea.seed.party_backfill")

# The applicant's own role depends on what kind of account it is.
_APPLICANT_ROLE = {
    "trust": None,               # a trust's parties are its trustees, not the trust itself
    "entity_llc": None,
    "entity_corp": None,
    "entity_partnership": None,
    "custodial_utma": PartyRole.CUSTODIAN,
    "custodial_ugma": PartyRole.CUSTODIAN,
    "estate_inherited": PartyRole.EXECUTOR,
}
_TRUST_TYPES = {"trust"}
_ENTITY_TYPES = {"entity_llc", "entity_corp", "entity_partnership"}


async def backfill() -> None:
    async with SessionLocal() as s:
        # The table is created by the API's bootstrap; ensure it exists when run standalone.
        from app.core.db import Base, engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if not firm:
            log.error("backfill_no_firm")
            return

        cases = (await s.execute(
            select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
        )).scalars().all()

        added_total = 0
        for case in cases:
            current = (await s.execute(
                select(OnboardingParty).where(OnboardingParty.case_id == case.id)
            )).scalars().all()
            existing = {(p.role, p.legal_name) for p in current}
            # Also track names regardless of role: a person already on the case as a
            # beneficial owner must not be re-added as a joint owner because their name
            # also appears in the free-text `associated_parties` list.
            known_names = {p.legal_name for p in current}
            added: list[str] = []

            def add(role: str, name: str, *, unique_person: bool = False, **kw) -> None:
                nonlocal added_total
                if not name or (role, name) in existing:
                    return
                if unique_person and name in known_names:
                    return
                existing.add((role, name))
                known_names.add(name)
                s.add(OnboardingParty(
                    firm_id=firm.id, case_id=case.id, role=role, legal_name=name,
                    screening_status="not_screened", **kw,
                ))
                added.append(f"{role}:{name}")
                added_total += 1

            reg = case.registration_type or "individual"
            intake = case.intake or {}

            # 1. Existing beneficial owners keep their ownership data.
            for bo in (await s.execute(
                select(BeneficialOwner).where(BeneficialOwner.case_id == case.id)
            )).scalars().all():
                add(PartyRole.BENEFICIAL_OWNER, bo.legal_name,
                    dob=bo.dob, address=bo.address, id_number=bo.id_number,
                    ownership_pct=bo.ownership_pct,
                    is_control_person=bo.is_control_person, notes=bo.notes)

            # 2. The applicant, where the applicant is a natural person.
            applicant_role = _APPLICANT_ROLE.get(reg, PartyRole.OWNER)
            if applicant_role:
                add(applicant_role, case.prospect_name)

            # 3. Names that were only ever free text on the intake form. The right role
            #    depends on the registration type, and anyone already recorded (typically
            #    as a beneficial owner) is left alone rather than duplicated.
            if reg in _TRUST_TYPES:
                assoc_role = PartyRole.TRUSTEE
            elif reg in _ENTITY_TYPES:
                assoc_role = PartyRole.AUTHORISED_SIGNER
            else:
                assoc_role = PartyRole.JOINT_OWNER
            for name in intake.get("associated_parties", []) or []:
                add(assoc_role, name, unique_person=True)
            for name in intake.get("poa_holders", []) or []:
                add(PartyRole.POA_HOLDER, name)
            beneficiary = intake.get("beneficiary_name")
            if beneficiary:
                add(PartyRole.BENEFICIARY, beneficiary)
            for name in intake.get("beneficiaries", []) or []:
                add(PartyRole.BENEFICIARY, name)

            if added:
                log.info("backfill_case", case=case.prospect_name, added=added)

        await s.commit()

        # Report the resulting gate state.
        from app.aurea_core import parties as parties_core
        print(f"\nAdded {added_total} party record(s).\n")
        print(f"{'case':32}{'parties':9}{'screened':10}{'gate'}")
        print("-" * 68)
        for case in sorted(cases, key=lambda c: c.prospect_name):
            rows = (await s.execute(
                select(OnboardingParty).where(OnboardingParty.case_id == case.id)
            )).scalars().all()
            st = parties_core.completeness_for(case.registration_type, list(rows))
            gate = "BLOCKS" if st["blocks_activation"] else "clear"
            print(f"{case.prospect_name:32}{len(rows):<9}"
                  f"{str(st['screened_count']) + '/' + str(len(rows)):<10}{gate}")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
