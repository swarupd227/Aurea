"""India, the demonstration households' figures made coherent (B-400), and two emergency funds.

Since essential spending was recorded for the invented families (B-393) the retirement projection read them against saving
far too small for their incomes, so a family on Rs 96 lakh a year showed an 11% likelihood. The spending, the saving
recorded on the goals and the goals' own targets are restated in `seed/india_demo.py` and `seed/india_planning_fixes.py`
(`reset_goals` carries the goals to a deployed firm, and leaves one a person changed). This step carries the rest, and
changes only what a seed wrote:

  - an essential-spending figure still standing as the planning seed recorded it is replaced by the restated figure, the
    old one kept on file;
  - the retired Subir Banerjee gets the pension, and the Kapoor trustee the rent, that their incomes come from, where
    no income stream was ever on file for them;
  - the Iyers and the Sharmas get an emergency fund record (one complete, one with nothing set aside yet), where none is;
  - the minor the onboarding measures' seed added (Diya Iyer) is marked next generation while she is under eighteen, so
    the retirement plan stops counting a child as an adult and running the couple's plan to her plan-to age.

Nothing a person recorded is replaced or added over. Every record is invented and says so. Run by the seed when it builds
the firm and by `seed.india_demo_backfill` for a firm already deployed; a second run writes nothing.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, financial_plan, firm_time, planning_measures
from app.models.balance_sheet import BalanceItem
from app.models.emergency_fund import EmergencyFund
from app.models.enums import UserRole
from app.models.financial_plan import IncomeStream
from app.models.graph import Household, Person
from app.models.tenant import Firm
from seed import india_planning_fixes

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India figures (demonstration)"
MINOR = "Diya Iyer"
# What the planning seed first recorded, by household: only a figure still standing at this amount, recorded by it, is replaced.
FIRST_SPENDING = {"The Sharma Family": 2_400_000, "The Iyer Family": 4_800_000, "The D'Souza Household": 1_500_000,
                  "The Kapoor Family Trust": 3_000_000}
# (person, kind, a year, the age it started from, name, the paper it is read from). Each is already being paid.
STREAMS = [
    ("Subir Banerjee", "employer_pension", 420_000, 60, "Pension (demonstration)", "Pension payment statement"),
    ("Nandini Kapoor", "rental", 1_800_000, 60, "Rent from let premises (demonstration)", "Lease and rent receipts"),
]
# household -> (target, set aside, where, the deposit to record first where the money is not on file yet)
FUNDS = {
    "The Iyer Family": (1_800_000, 1_800_000, "Sweep deposit, a demonstration bank (invented)",
                        "Sweep deposit, a demonstration bank (invented)"),
    "The Sharma Family": (900_000, 0, None, None),
}


async def add(s: AsyncSession, firm: Firm) -> int:
    """How many records it wrote or replaced."""
    today = firm_time.today(firm)
    stated = today - timedelta(days=15)
    households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    written = 0
    for home, first in FIRST_SPENDING.items():
        household = households.get(home)
        if household is None:
            continue
        row = (await s.execute(select(BalanceItem).where(
            BalanceItem.firm_id == firm.id, BalanceItem.household_id == household.id, BalanceItem.kind == "essential_spending",
            BalanceItem.status == "current", BalanceItem.recorded_by == india_planning_fixes.BY))).scalars().first()
        if row is None or float(row.amount) != first:
            continue
        await balance_sheet.record_item(s, household.id, firm_id=firm.id, by=BY, kind="essential_spending", name=row.name,
                                        amount=india_planning_fixes.ESSENTIAL_SPENDING[home], as_of=today, source_document=SOURCE,
                                        replaces_id=row.id)
        written += 1

    for who, kind, annual, start_age, name, paper in STREAMS:
        person = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == who,
                                                       Person.deceased_on.is_(None)))).scalars().first()
        if person is None or (await s.execute(select(IncomeStream.id).where(
                IncomeStream.firm_id == firm.id, IncomeStream.person_id == person.id))).first():
            continue
        await financial_plan.record_stream(s, person.id, firm_id=firm.id, by=BY, kind=kind, name=name, annual_amount=annual,
                                           start_age=start_age, inflation_linked=True, source_document=f"{paper} ({SOURCE})")
        written += 1

    child = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == MINOR,
                                                  Person.is_next_gen.is_(False)))).scalars().first()
    if child is not None and (child.kyc or {}).get("demonstration") and child.date_of_birth and child.date_of_birth > today - timedelta(days=18 * 365 + 4):
        child.is_next_gen = True
        written += 1

    for home, (target, held, where, deposit) in FUNDS.items():
        household = households.get(home)
        if household is None or (await s.execute(select(EmergencyFund.id).where(
                EmergencyFund.firm_id == firm.id, EmergencyFund.household_id == household.id))).first():
            continue
        if deposit and not (await s.execute(select(BalanceItem.id).where(
                BalanceItem.firm_id == firm.id, BalanceItem.household_id == household.id, BalanceItem.name == deposit))).first():
            await balance_sheet.record_item(s, household.id, firm_id=firm.id, by=BY, kind="cash", name=deposit, amount=held, as_of=stated,
                                            source_document=f"Deposit statement ({SOURCE})")
            written += 1
        try:
            await planning_measures.record_fund(s, firm, UserRole.ADVISER, by=BY, household_id=household.id, target_amount=target,
                                                held_amount=held, held_in=where, statement_date=stated,
                                                document=f"Statement and planning note ({SOURCE})")
        except planning_measures.MeasureError:
            continue
        written += 1
    await s.flush()
    return written
