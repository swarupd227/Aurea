"""India polish on the demonstration firm: reminders raised, a KYC that holds orders, a performance fee line, an onboarding case's
guardian ready to confirm, a minor now an adult, and a segment minimum in rupees (B-247, B-255, B-259, B-261, B-262, B-273).

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm is built and
from `seed.india_demo_backfill` on a firm built before; it adds nothing where Tara Menon is already there. Dates are days before the
firm's today, so nothing ages.

  Tara Menon            18 years and a few weeks old, her guardian (her father) on file, no conversion recorded: "now an adult: convert",
                        a task for her adviser, and orders on her account refused until a person records the conversion
  Kabir Menon's case    an onboarding case that names his father as guardian: read_case_facts lays it out, and confirming it would record
                        the guardian (nothing is recorded here, so the page shows what is waiting)
  A performance fee     Karthik Iyer's estimate for the bill, confirmed as its own line, where the estimate clears the hurdle
  Segments              the firm's minimums stated in rupees, with the currency on each row
  Tasks                 the dates that are overdue or near become tasks once (Lakshmi Iyer's and Nandini Kapoor's KYC, the cyber policy review)
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, guardianship, india_reminders, pms
from app.models.enums import ClientSegment, MandateType
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingDocument
from app.models.tenant import Firm, FirmSegment
from seed.india_row7 import NAME as MENONS, SOURCE
from seed.india_row8 import BY as PMS_BY

BY = "seed: india polish (demonstration)"
RUPEES = {"private_wealth": 50_000_000, "mass_affluent": 10_000_000, "institutional": 500_000_000}
PERFORMANCE_FIXTURE = "performance fee line"


async def add_segments(s: AsyncSession, firm: Firm) -> int:
    """The demonstration firm states its minimums in rupees. Other rows of any firm are given the currency they always had."""
    n = 0
    for seg in (await s.execute(select(FirmSegment).where(FirmSegment.firm_id == firm.id))).scalars().all():
        if seg.currency is not None:
            continue
        if seg.slug in RUPEES:
            seg.min_aum_usd, seg.currency = RUPEES[seg.slug], "INR"
            n += 1
        elif seg.min_aum_usd is not None:
            seg.min_aum_usd, seg.currency = None, None      # a dollar figure on an Indian firm is no minimum at all: cleared, not converted
            n += 1
    return n


async def add(s: AsyncSession, firm: Firm) -> int:
    menons = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == MENONS))).scalar_one_or_none()
    if menons is None:
        return 0
    written = await add_segments(s, firm)
    if (await s.execute(select(Person.id).where(Person.firm_id == firm.id, Person.full_name == "Tara Menon"))).first():
        return written
    today = firm_time.today(firm)
    adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == "adviser"))).scalars().first()
    vikram = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Vikram Menon"))).scalar_one_or_none()
    kabir = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Kabir Menon"))).scalar_one_or_none()
    if vikram is None or kabir is None:
        return written

    # Tara: an adult for a few weeks, her father still her guardian on the account.
    born = today - timedelta(days=18 * 365 + 5 + 24)
    tara = Person(firm_id=firm.id, household_id=menons.id, full_name="Tara Menon", preferred_name="Tara", date_of_birth=born,
                  segment=ClientSegment.PRIVATE_WEALTH, kyc={"status": "verified", "id_verified": True, "aml_screened": True, "demonstration": True},
                  profile={"risk_profile": "balanced", "tax": {}})
    s.add(tara)
    await s.flush()
    if adviser is not None:
        s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="person", to_id=tara.id))
    mandate = Mandate(firm_id=firm.id, person_id=tara.id, name="Tara Menon - Savings", mandate_type=MandateType.ADVISORY,
                      suitability={"risk_profile": "balanced", "capacity_for_loss": "medium", "values_exclusions": []},
                      constraints={"service_track": "investment_adviser"})
    s.add(mandate)
    await s.flush()
    s.add(Account(firm_id=firm.id, mandate_id=mandate.id, name="Tara Menon demat", custodian="CDSL", currency="INR", cash_balance=150_000,
                  registration_type="individual"))
    await guardianship.record(s, firm, by=BY, minor_person_id=tara.id, guardian_person_id=vikram.id, guardian_name="Vikram Menon",
                              relationship="father", document=f"Birth certificate naming the father ({SOURCE})")
    written += 4

    # Kabir's onboarding case names his father as guardian; nothing is recorded from it until a person confirms.
    if not (await s.execute(select(OnboardingCase.id).where(OnboardingCase.firm_id == firm.id, OnboardingCase.prospect_name == "Kabir Menon"))).first():
        case = OnboardingCase(firm_id=firm.id, prospect_name="Kabir Menon", is_entity=False, registration_type="minor_guardian",
                              segment=ClientSegment.PRIVATE_WEALTH, status="approved", sla_days=10,
                              intake={"registration": {"guardian_name": "Vikram Menon", "guardian_relationship": "father"}},
                              materialized={"person_id": str(kabir.id), "household_id": str(menons.id)}, notes=SOURCE)
        s.add(case)
        await s.flush()
        s.add(OnboardingDocument(firm_id=firm.id, case_id=case.id, doc_type="guardian_proof", filename="guardian_proof_kabir_menon.pdf",
                                 raw_text=f"Birth certificate naming Vikram Menon as father of Kabir Menon. {SOURCE}."))
        written += 2

    # A performance fee the person confirmed for the bill, where the estimate clears the hurdle; otherwise there is nothing to confirm.
    try:
        karthik = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Karthik Iyer"))).scalar_one_or_none()
        m = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id == karthik.id))).scalars().first() if karthik else None
        if m is not None:
            await pms.record_fee_line(s, firm, by=PMS_BY, mandate_id=m.id, note=f"Confirmed for the demonstration ({SOURCE})")
            written += 1
    except pms.PmsError:
        pass

    await s.flush()
    written += (await india_reminders.raise_due(s, firm)).get("raised", 0)
    return written
