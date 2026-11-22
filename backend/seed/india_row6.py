"""India row 6 on the demonstration firm: what is on file before advice, fees against SEBI's cap, and the calendar.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the
firm is built and from `seed.india_demo_backfill` on a firm built before row 6; it adds nothing where row 6 is
already on file.

  The Sharmas   everything before advice is on file, and the family is billed 1% of assets, within the cap.
  Banerjee      signed, disclosed and profiled, but the client has not consented to the risk profile: proposals wait.
  D'Souza       everything on file, on a fixed fee of Rs 1,80,000 a year, above the Rs 1,51,000 cap: the bill is held.
  Calendar      October's complaint data filed; October's portfolio-manager report left unfiled, so it reads overdue.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import billing, compliance_calendar, firm_time, pre_advice, risk_profile
from app.core.db import utcnow
from app.models.compliance_calendar import ComplianceFiling
from app.models.consent_registry import INDIA_KINDS, ConsentRecord
from app.models.graph import Household
from app.models.onboarding import FeeSchedule

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 6 (demonstration)"
# The middle answer to every question: a balanced tolerance and a medium capacity.
ANSWERS = {q["key"]: q["options"][1] for q in risk_profile.QUESTIONS}
FILED_ON = date(2026, 11, 6)
REGISTRATION = {
    "registration_number": "INA000000000 (demonstration, not a real registration)",
    "registered_address": "Demonstration address, Mumbai",
    "sebi_regional_office": "Western Regional Office, Mumbai (demonstration)",
    "principal_officer": "Ananya Rao (demonstration)",
    "compliance_officer": "Vikram Menon (demonstration)",
    "grievance_contact": "grievances@astra-india.demo (demonstration)",
}


async def _household(s: AsyncSession, firm, name: str) -> Household:
    return (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == name))).scalar_one()


def _consent(firm, household, kind: str, method: str, day, ref: str) -> ConsentRecord:
    who = household.name.replace("The ", "")
    return ConsentRecord(firm_id=firm.id, household_id=household.id, kind=kind, status="granted", given_by=who, given_on=day,
                         method=method, evidence=f"{ref} ({SOURCE})", recorded_by=BY)


async def _schedule(s: AsyncSession, firm, code: str, name: str, **terms) -> FeeSchedule:
    row = FeeSchedule(firm_id=firm.id, code=code, name=name, currency="INR", notes=SOURCE, **terms)
    s.add(row)
    await s.flush()
    return row


async def add(s: AsyncSession, firm) -> int:
    done = (await s.execute(select(ConsentRecord.id).where(
        ConsentRecord.firm_id == firm.id, ConsentRecord.kind.in_(INDIA_KINDS)).limit(1))).first()
    if done:
        return 0
    today = firm_time.today(firm)
    added = 0

    sharma = await _household(s, firm, "The Sharma Family")
    banerjee = await _household(s, firm, "The Banerjee Household")
    dsouza = await _household(s, firm, "The D'Souza Household")

    # Questionnaires first: a consent to the risk profile covers the answers on file on the day it was given.
    for h in (sharma, banerjee, dsouza):
        await risk_profile.submit(s, h.id, ANSWERS, firm_id=firm.id, by=BY, role="adviser")
        added += 1

    for h, kinds in ((sharma, INDIA_KINDS), (dsouza, INDIA_KINDS),
                     (banerjee, ("advisory_agreement", "ai_use_disclosure", "investor_charter"))):
        for kind in kinds:
            method = {"advisory_agreement": "e_signature", "risk_profile": "email", "non_sebi_declaration": "e_signature"}.get(kind, "email")
            ref = {"advisory_agreement": f"E-sign envelope IA-{h.name.split()[1][:3].upper()}-0412",
                   "risk_profile": "Reply from the registered email confirming the profile",
                   "non_sebi_declaration": "Signed declaration, onboarding pack page 9"}.get(kind, "Sent to the registered email")
            s.add(_consent(firm, h, kind, method, today, ref))
            added += 1

    # Fees: the Sharmas and Banerjees at 1% of assets, the D'Souzas on a fixed fee above the cap.
    aua = await _schedule(s, firm, "IA-AUA-100", "Advisory fee, 1% of assets a year", fee_type="flat_bps", flat_bps=100)
    fixed = await _schedule(s, firm, "IA-FIXED-180K", "Advisory fee, fixed Rs 1,80,000 a year", fee_type="flat_fee", flat_fee=180000)
    start = today.replace(day=1) - timedelta(days=95)
    for h, schedule, householding in ((sharma, aua, True), (banerjee, aua, False), (dsouza, fixed, False)):
        for m in await pre_advice.adviser_mandates(s, firm, h.id):
            await billing.activate_arrangement(
                s, firm_id=firm.id, mandate=m, fee_schedule_id=schedule.id, billing_method="arrears",
                billing_frequency="quarterly", householding=householding, effective_from=start,
                set_by="Ananya Rao (demonstration)", set_at=utcnow(), confirmed_by="Vikram Menon (demonstration)",
                confirmed_at=utcnow(), source="onboarding", notes=SOURCE)
            added += 1

    # The calendar: October's complaint data filed on time; October's portfolio-manager report left owed.
    if today >= FILED_ON:
        s.add(ComplianceFiling(firm_id=firm.id, obligation="complaints_monthly", period="2026-10", due_on=date(2026, 11, 7),
                               filed_on=FILED_ON, reference=f"Published on the website and app homepage ({SOURCE})", recorded_by=BY))
        added += 1
    settings = dict(firm.settings or {})
    settings["sebi_registration"] = {**REGISTRATION, **(settings.get("sebi_registration") or {})}
    firm.settings = settings
    await s.flush()
    assert compliance_calendar.applies(firm)
    return added
