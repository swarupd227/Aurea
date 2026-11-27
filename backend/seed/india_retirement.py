"""India's retirement stack on the demonstration firm (B-395): passbook and statement lines, monthly contributions (an
employer's NPS among them) and one quoted annuity rate.

Every record is invented for the demonstration firm and says so in its source. Each goes in through the module a person's
entry goes through, so it is refused for the same reasons. Where `seed/india_row5.py` had recorded an EPF, PPF or NPS
account as a pension total on net worth, that total is ended and the same amount is recorded as a statement line, which
the projection reads, so net worth counts it once and stays as it was. The families stand for different answers: Meera
Sharma has a quoted annuity rate, so her NPS annuity is worked, and Rajesh Sharma and Karthik Iyer have none, so theirs
is not assessed. Nobody is recorded in the Unified Pension Scheme. Run by the seed when it builds the firm and by
`seed.india_demo_backfill` for a firm already deployed; it adds only what is missing, and a line, contribution or quote a
person has since recorded or ended is left alone.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.aurea_core import balance_sheet, firm_time, india_savings, retirement_stack
from app.models.balance_sheet import BalanceItem
from app.models.enums import UserRole
from app.models.graph import Person
from app.models.held_away import HeldAwayHolding
from app.models.retirement_stack import RetirementRecord
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India retirement stack (demonstration)"
EPFO = "Provident fund office (demonstration)"
PENSION_MANAGER = "A demonstration pension fund manager (invented)"
BANK = "A demonstration bank (invented)"

# (person, kind, institution, value, extras, the name of the row-5 pension total this line takes the place of)
LINES = [
    ("Rajesh Sharma", "epf", EPFO, 1_850_000, {"rate": 8.25, "details": {"employer": "Sharma Industrial Components Pvt Ltd (demonstration)"}},
     "EPF (Employees' Provident Fund)"),
    ("Rajesh Sharma", "nps", PENSION_MANAGER, 900_000, {"details": {"tier": "Tier I", "equity_share_percent": 50}}, None),
    # A maturity `MATURES_IN` days out, so the account shows as joining the portfolio when it matures.
    ("Meera Sharma", "ppf", BANK, 920_000, {"rate": 7.1, "matures_in": 2_900}, "PPF (Public Provident Fund)"),
    ("Karthik Iyer", "nps", PENSION_MANAGER, 2_600_000, {"details": {"tier": "Tier I", "equity_share_percent": 75}}, "NPS Tier I"),
]
# (person, scheme, payer, a month)
CONTRIBUTIONS = [
    ("Rajesh Sharma", "epf", "own", 25_000),
    ("Rajesh Sharma", "epf", "employer", 23_750),
    ("Rajesh Sharma", "ppf", "own", 12_500),
    ("Rajesh Sharma", "nps", "employer", 20_000),
    ("Meera Sharma", "nps", "own", 5_000),
    ("Meera Sharma", "ppf", "own", 5_000),
    ("Karthik Iyer", "nps", "own", 10_000),
    ("Karthik Iyer", "nps", "employer", 30_000),
]
# (person, rate, terms)
QUOTES = [("Meera Sharma", 6.2, "Joint life with return of purchase price (demonstration)")]


async def add(s, firm: Firm) -> int:
    """The lines, contributions and quote not on file yet. Returns how many records it wrote."""
    today = firm_time.today(firm)
    stated = today - timedelta(days=20)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    written = 0
    for name, kind, institution, value, extras, total in LINES:
        person = people.get(name)
        if person is None or person.deceased_on is not None:
            continue
        # Any line of this class for this person, standing or ended, from any source: theirs, so nothing is added.
        have = (await s.execute(select(HeldAwayHolding).where(
            HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.person_id == person.id))).scalars().all()
        if any((h.attributes or {}).get("savings_kind") == kind for h in have):
            continue
        extras = dict(extras)
        matures_in = extras.pop("matures_in", None)
        if matures_in:
            extras["maturity_on"] = (today + timedelta(days=matures_in)).isoformat()
        item = None
        if total:
            item = (await s.execute(select(BalanceItem).where(
                BalanceItem.firm_id == firm.id, BalanceItem.person_id == person.id, BalanceItem.kind == "pension",
                BalanceItem.name == total, BalanceItem.status == "current"))).scalars().first()
        if item is not None:
            value = float(item.amount)
            await balance_sheet.end_item(s, item.id, firm_id=firm.id, by=BY, reason="Recorded as a statement line, which stands for it")
        try:
            await india_savings.record(s, firm, UserRole.ADVISER, by=BY, household_id=person.household_id, person_id=person.id, kind=kind,
                                       institution=institution, value=value, value_on=stated, document=f"Statement, filed ({SOURCE})", **extras)
        except ValueError:
            continue
        written += 1

    # A contribution or quote ever recorded for this person, scheme and payer (current or ended) is left alone.
    ever = {(r.person_id, r.kind, r.scheme, r.payer) for r in (await s.execute(
        select(RetirementRecord).where(RetirementRecord.firm_id == firm.id))).scalars().all()}
    for name, scheme, payer, monthly in CONTRIBUTIONS:
        person = people.get(name)
        if person is None or (person.id, "contribution", scheme, payer) in ever:
            continue
        try:
            await retirement_stack.record_contribution(
                s, firm, UserRole.ADVISER, by=BY, person_id=person.id, scheme=scheme, payer=payer, monthly_amount=monthly,
                statement_date=stated, document=f"{'Payslip' if payer == 'employer' else 'Passbook'}, filed ({SOURCE})")
        except retirement_stack.StackError:
            continue
        written += 1
    for name, rate, terms in QUOTES:
        person = people.get(name)
        if person is None or (person.id, "annuity_rate", "nps", None) in ever:
            continue
        try:
            await retirement_stack.record_annuity_rate(
                s, firm, UserRole.ADVISER, by=BY, person_id=person.id, rate=rate, quote_date=stated, terms=terms,
                document=f"Annuity quote from a demonstration life insurer, filed ({SOURCE})")
        except retirement_stack.StackError:
            continue
        written += 1
    await s.flush()
    return written
