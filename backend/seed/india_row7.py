"""India row 7 on the demonstration firm: a non-resident household, a FATCA and CRS gap, and a minor with a guardian.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the
firm is built and from `seed.india_demo_backfill` on a firm built before row 7; it adds nothing where the household is
already there.

  The Menons   a non-resident Indian in Dubai and his wife: his residence and FATCA and CRS self-certification are on
               file, hers shows a US indicium and no self-certification, so the family waits before advice. The paperwork
               before advice is otherwise complete, so the gap is the one thing the page shows.
  Ira          a minor whose guardian (her father) is on file; her account is run by him.
  Kabir        a minor with no guardian on file: an order on his account would be refused, and the page says so.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, guardianship, markets, residency, risk_profile
from app.models.consent_registry import INDIA_KINDS
from app.models.enums import ClientSegment, MandateType
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TaxLot
from app.models.tenant import Firm
from seed import india_row6

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 7 (demonstration)"
NAME = "The Menon NRI Household"
ADVISER = "investment_adviser"


def _years_ago(today, years: int, month: int, day: int):
    return today.replace(year=today.year - years, month=month, day=day)


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(Household.id).where(Household.firm_id == firm.id, Household.name == NAME))).first():
        return 0
    today = firm_time.today(firm)
    year = markets.tax_year(markets.MARKETS["IN"], today)["label"]
    model = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id, ModelPortfolio.name == "Core Balanced"))).scalar_one_or_none()
    adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == "adviser"))).scalars().first()
    household = Household(firm_id=firm.id, name=NAME, segment=ClientSegment.PRIVATE_WEALTH, values={"jurisdiction": "IN"})
    s.add(household)
    await s.flush()
    kyc = {"status": "verified", "id_verified": True, "aml_screened": True, "demonstration": True}

    def person(name: str, dob) -> Person:
        p = Person(firm_id=firm.id, household_id=household.id, full_name=name, preferred_name=name.split()[0], date_of_birth=dob,
                   segment=ClientSegment.PRIVATE_WEALTH, kyc=kyc, profile={"risk_profile": "balanced", "tax": {}})
        s.add(p)
        return p

    vikram = person("Vikram Menon", _years_ago(today, 54, 5, 18))
    anita = person("Anita Menon", _years_ago(today, 51, 2, 3))
    ira = person("Ira Menon", _years_ago(today, 11, 9, 2))
    kabir = person("Kabir Menon", _years_ago(today, 8, 1, 14))
    await s.flush()
    if adviser is not None:
        for p in (vikram, anita, ira, kabir):
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="person", to_id=p.id))

    # One NRO demat on the adviser's track, held by Vikram, with a holding at the latest price on file.
    mandate = Mandate(firm_id=firm.id, person_id=vikram.id, name="Vikram Menon - Balanced", mandate_type=MandateType.ADVISORY,
                      suitability={"risk_profile": "balanced", "capacity_for_loss": "medium", "values_exclusions": []},
                      constraints={"service_track": ADVISER, "cgt_budget": 300000}, model_portfolio_id=model.id if model else None)
    s.add(mandate)
    await s.flush()
    account = Account(firm_id=firm.id, mandate_id=mandate.id, name="Vikram Menon NRO demat", custodian="CDSL", currency="INR",
                      cash_balance=2_500_000, registration_type="nri_nro")
    s.add(account)
    await s.flush()
    inst = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "NIFTYBEES.NS"))).scalar_one_or_none()
    price = (await s.execute(select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()))).scalars().first() if inst else None
    if inst and price:
        close = float(price.close)
        h = Holding(firm_id=firm.id, account_id=account.id, instrument_id=inst.id, quantity=6000, market_value=round(6000 * close, 2),
                    cost_basis=round(6000 * close * 0.82, 2), lineage={"source": "CDSL", "as_of": today.isoformat()}, confidence=0.95)
        s.add(h)
        await s.flush()
        s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=6000, cost_per_unit=round(close * 0.82, 2), acquired_on=today - timedelta(days=700)))
    written = 6

    # The paperwork before advice, all on file: only the FATCA and CRS gap is left.
    answers = {q["key"]: q["options"][1] for q in risk_profile.QUESTIONS}
    await risk_profile.submit(s, household.id, answers, firm_id=firm.id, by=BY, role="adviser")
    for kind in INDIA_KINDS:
        method = {"advisory_agreement": "e_signature", "non_sebi_declaration": "e_signature"}.get(kind, "email")
        s.add(india_row6._consent(firm, household, kind, method, today, f"{kind.replace('_', ' ').title()} on file for the Menons"))
        written += 1

    common = {"indian_citizen_or_pio": True, "fema_status": "nri", "country_of_residence": "United Arab Emirates",
              "tax_residencies": ["United Arab Emirates"], "days_in_india": 41, "days_prior_four_years": 170,
              "days_prior_seven_years": 310, "nonresident_years_of_prior_ten": 10, "left_for_employment_abroad": True,
              "india_income": 640000, "taxed_elsewhere": False}
    await residency.record(s, firm, by=BY, person_id=vikram.id, tax_year=year, document=f"Passport and residence visa scans, travel record ({SOURCE})",
                           accounts=["NRE", "NRO"], pis_permission=f"PIS permission letter, designated bank branch, Dubai ({SOURCE})",
                           us_person=False, self_certified_on=today - timedelta(days=30), **common)
    await residency.record(s, firm, by=BY, person_id=anita.id, tax_year=year, document=f"Passport and visa scans ({SOURCE})",
                           accounts=["NRO"], us_indicia=["us_address"], **common)
    written += 2
    await guardianship.record(s, firm, by=BY, minor_person_id=ira.id, guardian_person_id=vikram.id, guardian_name="Vikram Menon",
                              relationship="father", document=f"Birth certificate naming the father ({SOURCE})")
    return written + 1
