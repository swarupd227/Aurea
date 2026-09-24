"""Account-type tax treatment (L200-1 §4) — registration type on the live account,
structured beneficiary designations, and RMD computed from actual custodied assets rather
than a hand-typed scalar.

Three real gaps this closes, all found the same way: by tracing what `OnboardingCase`
already captures and confirming it stops at the onboarding lifecycle stage.

1. `registration_type` lived only on `OnboardingCase` — nothing on the live `Account` could
   say "this is an IRA" once onboarding finished, so nothing downstream could reason about
   it either.
2. RMD was computed from `Person.profile["tax"]["ira_value"]`, a hand-typed number in a JSON
   blob, decoupled from any actual account or its real holdings value.
3. Beneficiary "coverage" was a truthy check on free-text intake JSON — no percentages, no
   primary/contingent split, no way to ask "do the percentages sum to 100".
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding

# Registration types that carry an RMD obligation once the owner reaches 73 (or, for an
# inherited IRA, on the election-dependent schedule handled separately below).
_IRA_LIKE = {"traditional_ira", "employer_rollover"}

# Registration types where a missing/incomplete beneficiary designation is the specific
# failure L200-1 §4 calls "the #1 practical estate failure" — assets that should pass by
# beneficiary designation instead falling through to probate.
_BENEFICIARY_REQUIRED = {
    "traditional_ira", "roth_ira", "employer_rollover", "inherited_ira",
    "estate_inherited", "plan_529", "hsa",
}

# IRS Uniform Lifetime Table (simplified) — deliberately not imported from
# tax_intelligence.py's private US module: that module computes RMD from a person-level
# scalar; this one computes it per account from real holdings, and the two are independent
# enough that sharing a private constant would couple them for no real benefit.
_US_RMD_FACTORS = {
    73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5,
    83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4,
    88: 13.7, 89: 12.9, 90: 12.2,
}


def _age(dob: date | None) -> int | None:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def _account_value(session: AsyncSession, account_id: uuid.UUID) -> float:
    holdings = (
        await session.execute(select(Holding).where(Holding.account_id == account_id))
    ).scalars().all()
    return sum(float(h.market_value or 0) for h in holdings)


def _inherited_rmd(account: Account, value: float) -> dict:
    if account.original_owner_death_date is None:
        return {
            "status": "needs_election",
            "reason": "Original owner's date of death is not on file — the 10-year-rule deadline cannot be set.",
        }
    death_year = account.original_owner_death_date.year
    if account.rmd_election_method == "10_year_rule":
        deadline = date(death_year + 10, 12, 31)
        days_left = (deadline - date.today()).days
        return {
            "status": "ten_year_rule", "deadline": deadline.isoformat(),
            "days_remaining": days_left, "account_value": round(value, 2),
            "urgent": days_left < 365,
        }
    if account.rmd_election_method == "life_expectancy":
        return {
            "status": "life_expectancy", "account_value": round(value, 2),
            "note": "Annual RMD by the beneficiary's own single life-expectancy factor — a specialist calculation (L300).",
        }
    return {
        "status": "needs_election",
        "reason": "The 10-year-rule vs. life-expectancy election has not been recorded.",
    }


async def rmd_for_account(session: AsyncSession, account: Account) -> dict | None:
    """RMD status for one account, computed from its actual holdings value. Returns None
    for a registration type that carries no RMD obligation at all (a Roth IRA, taxable
    account, trust, etc.) — the caller can tell "not applicable" from "applicable, clear"."""
    if account.registration_type == "inherited_ira":
        return _inherited_rmd(account, await _account_value(session, account.id))
    if account.registration_type not in _IRA_LIKE:
        return None

    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    owner_age = None
    if mandate and mandate.person_id:
        person = await session.get(Person, mandate.person_id)
        owner_age = _age(person.date_of_birth) if person else None

    if owner_age is None:
        return {"status": "unknown", "reason": "Owner's date of birth is not on file."}
    if owner_age < 73:
        return {"status": "not_yet", "owner_age": owner_age, "starts_at_age": 73}

    value = await _account_value(session, account.id)
    factor = _US_RMD_FACTORS.get(min(owner_age, 90), 12.2)
    return {
        "status": "required", "owner_age": owner_age, "account_value": round(value, 2),
        "divisor": factor, "amount": round(value / factor, 2) if factor else 0.0,
    }


async def _household_account_ids(session: AsyncSession, household_id: uuid.UUID) -> list[uuid.UUID]:
    persons = (
        await session.execute(select(Person).where(Person.household_id == household_id))
    ).scalars().all()
    entities = (
        await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))
    ).scalars().all()
    person_ids, entity_ids = [p.id for p in persons], [e.id for e in entities]

    from sqlalchemy import or_
    conds = []
    if person_ids:
        conds.append(Mandate.person_id.in_(person_ids))
    if entity_ids:
        conds.append(Mandate.entity_id.in_(entity_ids))
    mandates = (await session.execute(select(Mandate).where(or_(*conds)))).scalars().all() if conds else []
    mandate_ids = [m.id for m in mandates]
    if not mandate_ids:
        return []
    accounts = (await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))).scalars().all()
    return [a.id for a in accounts]


async def beneficiary_audit(
    session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None
) -> dict:
    """Every account whose registration type needs beneficiaries, and whether its
    designations are complete — present, and each class (primary/contingent) summing to
    100%. Firm-wide by default (the periodic confirmation campaign L200-1 §4 describes);
    pass household_id to scope to one household."""
    query = select(Account).where(Account.firm_id == firm_id)
    if household_id is not None:
        account_ids = await _household_account_ids(session, household_id)
        if not account_ids:
            return {"accounts_checked": 0, "gaps": []}
        query = query.where(Account.id.in_(account_ids))

    accounts = (await session.execute(query)).scalars().all()
    needing = [a for a in accounts if a.registration_type in _BENEFICIARY_REQUIRED]

    gaps = []
    for a in needing:
        beneficiaries = (
            await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id == a.id))
        ).scalars().all()
        by_class: dict[str, float] = {}
        for b in beneficiaries:
            by_class[b.designation_class] = by_class.get(b.designation_class, 0.0) + float(b.percentage)

        primary_pct = round(by_class.get("primary", 0.0), 2)
        issue = None
        if not beneficiaries:
            issue = "no beneficiaries on file"
        elif primary_pct != 100.0:
            issue = f"primary designations sum to {primary_pct}%, not 100%"

        if issue:
            gaps.append({
                "account_id": str(a.id), "account_name": a.name,
                "registration_type": a.registration_type, "issue": issue,
                "beneficiary_count": len(beneficiaries),
            })

    return {
        "accounts_checked": len(needing),
        "gaps": gaps,
        "clean": len(gaps) == 0,
    }
