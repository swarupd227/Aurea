"""India tax planning on the demonstration firm (B-396): the tax facts the regime comparison now reads, stated returns for
the after-tax comparison, and a few reviews held.

Every record is invented for the demonstration firm and says so. Each goes in through the module a person's entry goes
through, so it is refused for the same reasons. Run by `seed.india_demo` when the firm is built and by
`seed.india_demo_backfill` for a firm already deployed; it adds only what is missing.

  The Sharmas   Rajesh has basic salary on file, so his employer's NPS is held to each regime's limit; his regime review is
                recorded. Meera's papers show no business income, and her harvest review is recorded.
  The Iyers     Lakshmi's return shows business income, so switching regimes is limited for her. Karthik has no basic salary
                on file, so his employer's NPS reads "limit not checked"; neither review is recorded.
  Everyone else has business income not on file and no review recorded. No gain-harvest task is raised here: the worker
                raises them from January, and a person may sooner.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.aurea_core import firm_time, india_after_tax, india_tax_year, markets, salary_tax
from app.models.enums import UserRole
from app.models.graph import Person
from app.models.india_tax_planning import TaxReview
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India tax planning (demonstration)"

# (person, the facts, the document)
TAX_FACTS = [
    ("Rajesh Sharma", {"basic_salary": 2_000_000}, "Salary slip"),
    ("Meera Sharma", {"business_income": 0}, "Filed return, no business schedule"),
    ("Lakshmi Iyer", {"business_income": 3_600_000}, "Filed return, business schedule"),
]
# (vehicle, name, return a year before tax in percent, document)
RETURNS = [
    ("deposit", "A demonstration bank's three-year term deposit (invented)", 7.0, "Rate sheet"),
    ("debt_fund", "A demonstration debt fund, growth and income plans (invented)", 7.4, "Factsheet"),
    ("arbitrage_fund", "A demonstration arbitrage fund, growth and income plans (invented)", 7.1, "Factsheet"),
    ("equity_fund", "A demonstration equity fund, growth and income plans (invented)", 11.0, "Investment committee note"),
]
# (person, kind, what was covered)
REVIEWS = [
    ("Rajesh Sharma", "regime", "Both regimes read with the employer's NPS contribution; the choice stays his"),
    ("Meera Sharma", "harvest", "Gains and losses on her lots read against the year's exemption; nothing decided"),
]


async def add(s, firm: Firm) -> int:
    """The facts, returns and reviews not on file yet. Returns how many records it wrote."""
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    written = 0
    for name, facts, document in TAX_FACTS:
        person = people.get(name)
        if person is None or person.deceased_on is not None:
            continue
        tax = (person.profile or {}).get("tax") or {}
        missing = {k: v for k, v in facts.items() if tax.get(k) is None}
        # A figure the income on file would contradict is left out: a person has changed that file since the seed.
        income, salary = tax.get("annual_income"), tax.get("salary_income")
        if not missing or any(income is not None and v > float(income) for v in missing.values()) \
                or ("basic_salary" in missing and salary is not None and missing["basic_salary"] > float(salary)):
            continue
        await salary_tax.record(s, firm, UserRole.ADVISER, by=BY, person_id=person.id, document=f"{document} ({SOURCE})", **missing)
        written += 1
    standing = await india_after_tax.standing_returns(s, firm.id)
    for vehicle, name, rate, document in RETURNS:
        if vehicle in standing:
            continue
        await india_after_tax.record_return(s, firm, UserRole.ADVISER, by=BY, vehicle=vehicle, name=name, rate_percent=rate,
                                            stated_on=today - timedelta(days=25), document=f"{document} ({SOURCE})")
        written += 1
    began: date = markets.tax_year(markets.MARKETS["IN"], today)["start"]
    for name, kind, covered in REVIEWS:
        person = people.get(name)
        if person is None or person.deceased_on is not None:
            continue
        if (await s.execute(select(TaxReview.id).where(TaxReview.firm_id == firm.id, TaxReview.person_id == person.id,
                                                       TaxReview.kind == kind).limit(1))).first() is not None:
            continue
        await india_tax_year.record_review(s, firm, UserRole.ADVISER, by=BY, person_id=person.id, kind=kind, covered=covered,
                                           reviewed_on=max(began, today - timedelta(days=14)), document=f"Meeting note ({SOURCE})")
        written += 1
    return written
