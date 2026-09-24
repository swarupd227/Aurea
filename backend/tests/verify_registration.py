"""Prove the account-type tax treatment engine (L200-1 §4): RMD computed from an
account's actual holdings value (not a hand-typed scalar) and gated correctly by owner
age and registration type; the inherited-IRA 10-year-rule deadline computed from the
original owner's date of death; and the beneficiary audit catching both "no beneficiaries
on file" and "primary designations don't sum to 100%" — while leaving a registration type
that doesn't require beneficiaries (a plain taxable account) alone entirely.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_registration
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import registration as engine
from app.core.db import Base
from app.models.graph import Account, AccountBeneficiary, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def dob_for_age(age: int) -> date:
    today = date.today()
    return date(today.year - age, today.month, today.day)


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    # _household_account_ids() unconditionally queries legal_entity even though this
    # scenario has none — the table still needs to exist for that query not to error.
    wanted = ["firm", "household", "person", "legal_entity", "mandate", "account",
              "account_beneficiary", "instrument", "holding"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        household = Household(id=uuid.uuid4(), firm_id=firm.id, name="Test Household")
        owner_75 = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=household.id,
                           full_name="Retiree", date_of_birth=dob_for_age(75))
        owner_60 = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=household.id,
                           full_name="Not Yet 73", date_of_birth=dob_for_age(60))
        s.add_all([firm, household, owner_75, owner_60])
        await s.flush()

        mandate_ira = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=owner_75.id,
                               name="Retiree IRA", mandate_type="advisory")
        mandate_young = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=owner_60.id,
                                 name="Young IRA", mandate_type="advisory")
        s.add_all([mandate_ira, mandate_young])
        await s.flush()

        inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha", asset_class="equity")
        s.add(inst)
        await s.flush()

        print("\n=== RMD required: age 75, computed from real holdings, not a typed scalar ===")
        ira_account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                               name="Retiree Traditional IRA", currency="USD",
                               cash_balance=Decimal("0"), registration_type="traditional_ira")
        s.add(ira_account)
        await s.flush()
        s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=ira_account.id, instrument_id=inst.id,
                       quantity=Decimal("1000"), market_value=Decimal("530000"), cost_basis=Decimal("400000")))
        await s.flush()

        rmd = await engine.rmd_for_account(s, ira_account)
        check("RMD status required at age 75", rmd["status"], "required")
        check("RMD account value matches the actual holding", rmd["account_value"], 530000.0)
        # IRS factor at 75 is 24.6 -> 530000 / 24.6 = 21544.72
        check("RMD amount computed from the real value / IRS factor", rmd["amount"], round(530000 / 24.6, 2))

        print("\n=== RMD not yet: owner is 60, ten years before it applies ===")
        young_account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_young.id,
                                 name="Young Traditional IRA", currency="USD",
                                 cash_balance=Decimal("0"), registration_type="traditional_ira")
        s.add(young_account)
        await s.flush()
        rmd_young = await engine.rmd_for_account(s, young_account)
        check("no RMD required before 73", rmd_young["status"], "not_yet")

        print("\n=== RMD not applicable: a Roth IRA never has one ===")
        roth_account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                                name="Retiree Roth IRA", currency="USD",
                                cash_balance=Decimal("0"), registration_type="roth_ira")
        s.add(roth_account)
        await s.flush()
        rmd_roth = await engine.rmd_for_account(s, roth_account)
        check("Roth IRA returns no RMD status at all", rmd_roth, None)

        print("\n=== Inherited IRA: 10-year-rule deadline from the original owner's death date ===")
        death_date = date.today() - timedelta(days=400)  # a bit over a year ago
        inherited = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                             name="Inherited from Mother", currency="USD", cash_balance=Decimal("0"),
                             registration_type="inherited_ira", original_owner_death_date=death_date,
                             rmd_election_method="10_year_rule")
        s.add(inherited)
        await s.flush()
        s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=inherited.id, instrument_id=inst.id,
                       quantity=Decimal("100"), market_value=Decimal("50000"), cost_basis=Decimal("40000")))
        await s.flush()
        rmd_inherited = await engine.rmd_for_account(s, inherited)
        check("ten-year-rule status", rmd_inherited["status"], "ten_year_rule")
        check("deadline is Dec 31 of the 10th year after death",
              rmd_inherited["deadline"], date(death_date.year + 10, 12, 31).isoformat())

        no_election = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                               name="Inherited, Unelected", currency="USD", cash_balance=Decimal("0"),
                               registration_type="inherited_ira", original_owner_death_date=death_date)
        s.add(no_election)
        await s.flush()
        rmd_unelected = await engine.rmd_for_account(s, no_election)
        check("an inherited IRA with no election recorded needs one", rmd_unelected["status"], "needs_election")

        print("\n=== Beneficiary audit: no beneficiaries, incomplete percentages, and a clean case ===")
        # No beneficiaries at all on ira_account (needs them — traditional_ira).
        # Incomplete: only 60% primary.
        incomplete = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                              name="Incomplete Beneficiaries IRA", currency="USD",
                              cash_balance=Decimal("0"), registration_type="traditional_ira")
        s.add(incomplete)
        await s.flush()
        s.add(AccountBeneficiary(firm_id=firm.id, account_id=incomplete.id, beneficiary_name="Child A",
                                  designation_class="primary", percentage=Decimal("60")))
        # Clean: 100% primary, split two ways.
        clean = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                         name="Clean Beneficiaries IRA", currency="USD",
                         cash_balance=Decimal("0"), registration_type="traditional_ira")
        s.add(clean)
        await s.flush()
        s.add_all([
            AccountBeneficiary(firm_id=firm.id, account_id=clean.id, beneficiary_name="Child A",
                                designation_class="primary", percentage=Decimal("50")),
            AccountBeneficiary(firm_id=firm.id, account_id=clean.id, beneficiary_name="Child B",
                                designation_class="primary", percentage=Decimal("50")),
        ])
        # A plain taxable account never needs beneficiaries at all — must not appear in the audit.
        taxable = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_ira.id,
                           name="Plain Taxable", currency="USD", cash_balance=Decimal("0"),
                           registration_type="individual")
        s.add(taxable)
        await s.flush()

        audit = await engine.beneficiary_audit(s, firm.id, household_id=household.id)
        gaps_by_name = {g["account_name"]: g for g in audit["gaps"]}

        # Accounts requiring beneficiaries in this household: ira_account, roth_account,
        # inherited, no_election, incomplete, clean = 6. (young_account is traditional_ira
        # too, under mandate_young / owner_60, also in this household -> 7.)
        check("clean account has no gap", "Clean Beneficiaries IRA" in gaps_by_name, False)
        check("account with no beneficiaries at all is flagged",
              gaps_by_name["Retiree Traditional IRA"]["issue"], "no beneficiaries on file")
        check("account with 60% primary is flagged as incomplete",
              "60.0%" in gaps_by_name["Incomplete Beneficiaries IRA"]["issue"], True)
        check("a plain taxable account is never in the audit at all", "Plain Taxable" in gaps_by_name, False)
        check("audit is not clean", audit["clean"], False)

        print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
        for f in FAIL:
            print(f"  FAILED: {f}")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
