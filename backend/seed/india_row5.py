"""India row 5 on the demonstration firm: planning and protection on Indian terms, from records a person would keep.

Every record here is invented for the demonstration firm and says so in its source. Nothing is estimated: each policy,
balance, pension, nomination and will is a record, as a person would have entered it from a document. Run by the seed
when it builds the firm and by `seed.india_demo_backfill` for a firm already deployed; it adds nothing twice.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models.balance_sheet import BalanceItem
from app.models.client_file import EstateDocument
from app.models.financial_plan import IncomeStream
from app.models.graph import Account, AccountBeneficiary, Person
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India row 5"


async def add(s, firm: Firm) -> int:
    """The row 5 records, once. Returns how many it wrote (0 when the firm already has them)."""
    if (await s.execute(select(func.count(ProtectionPolicy.id)).where(ProtectionPolicy.firm_id == firm.id))).scalar_one():
        return 0
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    accounts = {a.name: a for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    rajesh, meera, karthik = people.get("Rajesh Sharma"), people.get("Meera Sharma"), people.get("Karthik Iyer")
    if not (rajesh and meera and karthik):
        return 0
    today, written = date(2026, 11, 1), 0

    def policy(person: Person, kind: str, provider: str, **terms) -> ProtectionPolicy:
        return ProtectionPolicy(firm_id=firm.id, household_id=person.household_id, person_id=person.id, kind=kind,
                                provider=provider, currency="INR", status="in_force", source_document=SOURCE,
                                recorded_by=BY, **terms)

    term = policy(rajesh, "life", "Term insurer (demonstration)", sum_insured=15000000, annual_premium=28000,
                  expires_on=date(2039, 3, 31), nominee="Meera Sharma", nominee_relationship="spouse")
    s.add(term)
    await s.flush()
    s.add_all([
        # An accident-only disability rider on the term plan: listed, never counted against a gap for any cause.
        policy(rajesh, "tpd", "Term insurer (demonstration)", sum_insured=5000000, rider_of=term.id, cover_basis="accident_only",
               expires_on=date(2039, 3, 31)),
        # Employer group cover, named to a brother: he receives it for the legal heirs.
        policy(rajesh, "life", "Employer group term (demonstration)", sum_insured=3000000, group_cover=True,
               nominee="Anil Sharma", nominee_relationship="brother"),
        policy(meera, "critical_illness", "Health insurer (demonstration)", sum_insured=2500000, annual_premium=19000),
        # Written under the Married Women's Property Act: outside Karthik's estate, for his wife and children.
        policy(karthik, "life", "Term insurer (demonstration)", sum_insured=40000000, annual_premium=61000,
               expires_on=date(2044, 11, 30), mwp=True),
    ])
    written += 5

    def pension(person: Person, name: str, amount: float) -> BalanceItem:
        return BalanceItem(firm_id=firm.id, household_id=person.household_id, person_id=person.id, side="asset", kind="pension",
                           name=name, amount=amount, currency="INR", as_of=today, source_document=SOURCE, recorded_by=BY)

    s.add_all([pension(rajesh, "EPF (Employees' Provident Fund)", 1850000),
               pension(meera, "PPF (Public Provident Fund)", 920000),
               pension(karthik, "NPS Tier I", 2600000)])
    s.add(IncomeStream(firm_id=firm.id, household_id=rajesh.household_id, person_id=rajesh.id, kind="eps_pension",
                       name="EPS pension", annual_amount=36000, start_age=58, inflation_linked=False,
                       source_document=SOURCE, as_of=today, recorded_by=BY))
    written += 4

    # Rajesh's will names Meera and Aarav; his demat names his brother, so the client file flags it.
    s.add(EstateDocument(firm_id=firm.id, person_id=rajesh.id, kind="will", signed_on=date(2022, 6, 14), held_by="Family solicitor",
                         source_document=SOURCE, recorded_by=BY, named=["Meera Sharma", "Aarav Sharma"]))
    demat = accounts.get("Rajesh Sharma demat")
    if demat is not None:
        s.add(AccountBeneficiary(firm_id=firm.id, account_id=demat.id, beneficiary_name="Anil Sharma", relationship_to_owner="brother",
                                 designation_class="primary", percentage=100, date_designated=date(2019, 4, 2), notes=SOURCE))
    written += 2
    await s.flush()
    return written
