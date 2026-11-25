"""The L100 India items on the demonstration firm: accredited investors, an adviser's fee terms, the deposit and a wait for the Budget
(B-291, B-292, B-300; the ticket-size gates of B-290 read the book as it stands and need no seed).

Every record is invented for the demonstration firm and says so in its evidence; the certificate numbers and the accreditation agency are
invented. Dates are days before the firm's today, so nothing ages. Run from `seed.india_demo` when the firm is built and from
`seed.india_demo_backfill` on a firm built before it; it adds nothing where an accredited certificate or a fee term is already on file.

  Nandini Kapoor    a certificate in force, with the criteria it states laid out (her family's trust is not an individual, so no cap changes)
  Karthik Iyer      a certificate that ran out four months ago: it reads lapsed, which is not accredited, and the cap would reach him again
  The Sharmas       the agreement collects six months of fees ahead; the adviser elected the fixed-fee mode twenty months ago and switched
                    to the AUA mode thirteen months ago (before this financial year, so this year's cap is the AUA mode's)
  The Menons        a second-opinion fee on assets held under a distribution arrangement, within 2.5%, counted in what the family is charged
  The firm          a refundable deposit with IAASB, held in a liquid fund, from the deposit's receipt
  The rate card     the long-term capital gains rate on listed equity marked awaiting the Budget
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import accredited, adviser_fees, counsel_review, firm_time
from app.core.security import UserRole
from app.models.graph import Household, Person
from app.models.india_gates import AccreditedInvestor
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: L100 India items (demonstration)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(AccreditedInvestor.id).where(AccreditedInvestor.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    n = 0
    in_force, lapsed = people.get("Nandini Kapoor"), people.get("Karthik Iyer")
    if in_force is not None:
        await accredited.record(s, firm, UserRole.ADVISER, by=BY, person_id=in_force.id, certificate_ref="ACC/DEMO/2026/0412",
                                agency="A demonstration accreditation agency (invented)", valid_from=today - timedelta(days=200),
                                valid_to=today + timedelta(days=165),
                                criteria=["Net worth of Rs 7.5 crore or more, at least half in financial assets, as the certificate states it"],
                                document=f"Accreditation certificate, filed in the client's KYC folder ({SOURCE})")
        n += 1
    if lapsed is not None:
        await accredited.record(s, firm, UserRole.ADVISER, by=BY, person_id=lapsed.id, certificate_ref="ACC/DEMO/2024/0187",
                                agency="A demonstration accreditation agency (invented)", valid_from=today - timedelta(days=850),
                                valid_to=today - timedelta(days=120),
                                criteria=["Annual income of Rs 2 crore or more, as the certificate states it"],
                                document=f"Accreditation certificate, since lapsed ({SOURCE})")
        n += 1
    sharma, menon = homes.get("The Sharma Family"), next((h for name, h in homes.items() if "Menon" in name), None)
    if sharma is not None:
        await adviser_fees.record(s, firm, UserRole.ADVISER, by=BY, household_id=sharma.id, kind="advance", advance_months=6,
                                  effective_on=today - timedelta(days=600),
                                  document=f"Signed advisory agreement, clause on fees payable in advance ({SOURCE})")
        await adviser_fees.record(s, firm, UserRole.ADVISER, by=BY, household_id=sharma.id, kind="mode", mode="fixed",
                                  effective_on=today - timedelta(days=600), document=f"Adviser's election of the fixed-fee mode, filed with the agreement ({SOURCE})")
        await adviser_fees.record(s, firm, UserRole.ADVISER, by=BY, household_id=sharma.id, kind="mode", mode="aua",
                                  effective_on=today - timedelta(days=400), reason="The family's assets under advice grew, and the adviser moved to AUA mode",
                                  document=f"Adviser's note of the change of mode, countersigned by the client ({SOURCE})")
        n += 3
    if menon is not None:
        await adviser_fees.record(s, firm, UserRole.ADVISER, by=BY, household_id=menon.id, kind="second_opinion", assets=4_000_000, rate=0.01, fee=40_000,
                                  effective_on=today - timedelta(days=40),
                                  document=f"Engagement letter and invoice for a second opinion on assets held under a distribution arrangement ({SOURCE})")
        n += 1
    if not ((firm.settings or {}).get("sebi_registration") or {}).get("iaasb_deposit"):
        settings = dict(firm.settings or {})
        settings["sebi_registration"] = {**(settings.get("sebi_registration") or {}), "iaasb_deposit": "Rs 5,00,000 (demonstration)",
                                         "iaasb_deposit_held_in": "liquid fund",
                                         "iaasb_deposit_document": f"IAASB deposit receipt and the fund statement ({SOURCE})"}
        firm.settings = settings
        await s.flush()
        n += 1
    try:
        await counsel_review.record_watch(s, firm, UserRole.COMPLIANCE, by=BY, figure_key="in_cg_equity_lt_rate", state="awaiting_budget",
                                          reason=f"Waiting on the next Union Budget before the long-term equity rate is read again ({SOURCE})")
        n += 1
    except counsel_review.CounselError:      # already marked
        pass
    return n
