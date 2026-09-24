"""Infer registration_type on existing accounts from their name (L200-1 §4), for firms
that predate the column.

Deliberately does NOT seed beneficiary designations — leaving accounts that need them
without any is the real, current state of a book that has never run a beneficiary
campaign, and it's what gives the beneficiary-audit tool something genuine to find on
first use (the same reasoning corporate_actions_backfill.py uses for leaving its seeded
action unposted). One demo account is set up as a fully-compliant example and one as an
inherited-IRA needing an RMD election, so the feature has both a clean and a gap case to
show, not just gaps everywhere.

Idempotent: only touches accounts where registration_type is still NULL.

    python -m seed.registration_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Account, AccountBeneficiary, Mandate, Person
from app.models.tenant import Firm

log = get_logger("aurea.seed.registration_backfill")

# Ordered so a more specific match (e.g. "roth ira") wins over a looser one ("ira").
_KEYWORD_TYPES = [
    ("roth ira", "roth_ira"),
    ("roth", "roth_ira"),
    ("rollover ira", "employer_rollover"),
    ("rollover", "employer_rollover"),
    ("401k", "employer_rollover"),
    ("ira", "traditional_ira"),
    ("trust", "trust"),
    ("foundation", "daf"),
    ("529", "plan_529"),
    ("hsa", "hsa"),
    ("joint", "joint_jtwros"),
    ("utma", "custodial_utma"),
    ("ugma", "custodial_ugma"),
]


def _infer(name: str) -> str:
    lowered = name.lower()
    for keyword, reg_type in _KEYWORD_TYPES:
        if keyword in lowered:
            return reg_type
    return "individual"


async def backfill() -> None:
    async with SessionLocal() as s:
        accounts = (await s.execute(select(Account).where(Account.registration_type.is_(None)))).scalars().all()
        inferred: dict[str, int] = {}
        for a in accounts:
            reg = _infer(a.name)
            a.registration_type = reg
            inferred[reg] = inferred.get(reg, 0) + 1
            log.info("registration_inferred", account=a.name, registration_type=reg)

        # One fully-compliant example: the first traditional/roth IRA with a mandate whose
        # owner is a Person (so "spouse"/"child" relationships make sense), if any.
        ira_accounts = [a for a in accounts if a.registration_type in ("traditional_ira", "roth_ira")]
        demo_clean = demo_inherited = None
        for a in ira_accounts:
            if a.mandate_id is None:
                continue
            mandate = await s.get(Mandate, a.mandate_id)
            if not mandate or not mandate.person_id:
                continue
            owner = await s.get(Person, mandate.person_id)
            if not owner:
                continue
            if demo_clean is None:
                demo_clean = (a, owner)
            elif demo_inherited is None and a.id != demo_clean[0].id:
                demo_inherited = (a, owner)
            if demo_clean and demo_inherited:
                break

        # No account name happened to carry an IRA-style keyword (the real case for a demo
        # book of generically-named "Family A/C" / "Household A/C" accounts) — fall back to
        # any two person-owned accounts and register them as IRAs explicitly, rather than
        # silently shipping a beneficiary-audit / RMD feature with nothing to demonstrate.
        if demo_clean is None or demo_inherited is None:
            used_ids = {x[0].id for x in (demo_clean, demo_inherited) if x is not None}
            candidates = []
            for a in accounts:
                if a.mandate_id is None or a.id in used_ids:
                    continue
                mandate = await s.get(Mandate, a.mandate_id)
                if not mandate or not mandate.person_id:
                    continue
                owner = await s.get(Person, mandate.person_id)
                if not owner:
                    continue
                candidates.append((a, owner))
                if len(candidates) >= 2:
                    break
            if demo_clean is None and candidates:
                account, owner = candidates.pop(0)
                account.registration_type = "traditional_ira"
                inferred[account.registration_type] = inferred.get(account.registration_type, 0) + 1
                demo_clean = (account, owner)
            if demo_inherited is None and candidates:
                account, owner = candidates.pop(0)
                account.registration_type = "traditional_ira"
                inferred[account.registration_type] = inferred.get(account.registration_type, 0) + 1
                demo_inherited = (account, owner)

        if demo_clean:
            account, owner = demo_clean
            existing = (await s.execute(
                select(AccountBeneficiary).where(AccountBeneficiary.account_id == account.id)
            )).scalars().all()
            if not existing:
                s.add(AccountBeneficiary(
                    firm_id=account.firm_id, account_id=account.id,
                    beneficiary_name=f"{owner.full_name}'s spouse", relationship_to_owner="spouse",
                    designation_class="primary", percentage=100, per_stirpes=False,
                ))
                log.info("demo_beneficiary_seeded", account=account.name)

        if demo_inherited:
            account, _owner = demo_inherited
            if account.registration_type == "traditional_ira" and account.original_owner_death_date is None:
                account.registration_type = "inherited_ira"
                account.original_owner_death_date = date.today() - timedelta(days=200)
                # Left un-elected deliberately — a real, current gap for the RMD panel to surface.
                log.info("demo_inherited_seeded", account=account.name)

        await s.commit()
        print(f"\nInferred registration type on {len(accounts)} account(s): {inferred}\n")
        if demo_clean:
            print(f"Seeded a compliant beneficiary example on '{demo_clean[0].name}'.")
        if demo_inherited:
            print(f"Set up '{demo_inherited[0].name}' as an inherited IRA needing an RMD election.")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
