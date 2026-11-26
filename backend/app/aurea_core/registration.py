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

from app.aurea_core import firm_time, markets, nomination_audit, rmd
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding
from app.models.tenant import Firm

# Registration types whose owner takes an RMD from the start age their birth year sets (aurea_core/rmd.py); an
# inherited IRA follows the election-dependent schedule handled separately below.
_IRA_LIKE = rmd.IRA_GROUP

# Registration types where a missing/incomplete beneficiary designation is the specific
# failure L200-1 §4 calls "the #1 practical estate failure" — assets that should pass by
# beneficiary designation instead falling through to probate.
_BENEFICIARY_REQUIRED = {
    "traditional_ira", "roth_ira", "employer_rollover", "inherited_ira",
    "estate_inherited", "plan_529", "hsa",
}


def _age(dob: date | None, today: date) -> int | None:
    """Age on the firm's date (firm_time.today): an RMD age turns over on the firm's day."""
    if not dob:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def _account_value(session: AsyncSession, account_id: uuid.UUID) -> float:
    holdings = (
        await session.execute(select(Holding).where(Holding.account_id == account_id))
    ).scalars().all()
    return sum(float(h.market_value or 0) for h in holdings)


def _inherited_rmd(account: Account, value: float, today: date) -> dict:
    if account.original_owner_death_date is None:
        return {
            "status": "needs_election",
            "reason": "Original owner's date of death is not on file — the 10-year-rule deadline cannot be set.",
        }
    death_year = account.original_owner_death_date.year
    if account.rmd_election_method == "10_year_rule":
        deadline = date(death_year + 10, 12, 31)
        days_left = (deadline - today).days
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
    today = await firm_time.today_of(session, account.firm_id)
    if account.registration_type == "inherited_ira":
        return _inherited_rmd(account, await _account_value(session, account.id), today)
    if account.registration_type not in _IRA_LIKE:
        return None

    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    person = await session.get(Person, mandate.person_id) if mandate and mandate.person_id else None
    if person is None or person.date_of_birth is None:
        return {"status": "unknown", "reason": "Owner's date of birth is not on file."}
    # The table, the start age and the 31 December value are aurea_core/rmd.py's (B-170).
    due = await rmd.account_due(session, account, person.date_of_birth, today)
    return {**due, "owner_id": str(person.id), "owner_name": person.preferred_name or person.full_name}


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
    pass household_id to scope to one household. An Indian firm's check is of nominations
    on the accounts a person holds (nomination_audit.py). With nothing checked the state
    is "nothing_checked" and it is never clean."""
    account_ids = await _household_account_ids(session, household_id) if household_id is not None else None
    firm = await session.get(Firm, firm_id)
    market = markets.for_firm(firm) if firm else None
    if market is not None and market.code == "IN":
        return _stated(await nomination_audit.audit(session, firm, account_ids))

    query = select(Account).where(Account.firm_id == firm_id)
    if account_ids is not None:
        query = query.where(Account.id.in_(account_ids or [uuid.uuid4()]))

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

    return _stated({
        "market": market.code if market else None,
        "accounts_total": len(accounts),
        "accounts_checked": len(needing),
        "accounts_covered": len(needing) - len(gaps),
        "gaps": gaps,
        "not_checked": [],
    })


def _stated(result: dict) -> dict:
    """The one reading of a check: nothing checked is its own answer, and never "all covered"."""
    state = "gaps" if result["gaps"] else "covered" if result["accounts_checked"] else "nothing_checked"
    return {**result, "state": state, "clean": state == "covered"}
