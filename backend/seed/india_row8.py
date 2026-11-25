"""India row 8 on the demonstration firm: a Portfolio Manager's client with an agreement on file, and one without.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm
is built and from `seed.india_demo_backfill` on a firm built before row 8; it adds nothing where an agreement is already
recorded.

  Karthik Iyer          a discretionary client of Rs 1.2 crore: segregated, two benchmarks, 1% fixed and 20% over a 10% hurdle on
                        the client's own high-water mark, operating expenses 0.40%. Every check passes, and the bill carries the
                        performance fee it would give, beside the fixed one.
  Kapoor Family Trust   a discretionary client with no agreement recorded: not assessed, and nothing held.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import billing, firm_time, pms
from app.core.db import utcnow
from app.models.graph import Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.pms import PmsAgreement
from app.models.tenant import Firm
from seed.india_row6 import COMPLIANCE_OFFICER

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 8 (demonstration)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(PmsAgreement.id).where(PmsAgreement.firm_id == firm.id).limit(1))).first():
        return 0
    karthik = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Karthik Iyer"))).scalar_one_or_none()
    if karthik is None:
        return 0
    mandate = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id == karthik.id))).scalars().first()
    if mandate is None:
        return 0
    today = firm_time.today(firm)
    await pms.record(s, firm, by=BY, mandate_id=mandate.id, strategy="Astra India Growth Strategy (demonstration)", kind="discretionary",
                     signed_on=today - timedelta(days=420), document=f"Signed portfolio management agreement, Annexure A ({SOURCE})",
                     initial_corpus=12_000_000, segregated=True, benchmarks=["Nifty 50 TRI", "Nifty 500 TRI"], fee_type="hybrid",
                     fixed_rate=0.01, performance_rate=0.20, hurdle_rate=0.10, high_water_mark=True, operating_expense_rate=0.004,
                     exit_load=[0.02, 0.01, 0.0])
    schedule = FeeSchedule(firm_id=firm.id, code="PMS-HYBRID-100", name="Portfolio management, 1% of assets and a performance fee",
                           currency="INR", notes=SOURCE, fee_type="flat_bps", flat_bps=100)
    s.add(schedule)
    await s.flush()
    await billing.activate_arrangement(
        s, firm_id=firm.id, mandate=mandate, fee_schedule_id=schedule.id, billing_method="arrears", billing_frequency="quarterly",
        householding=False, effective_from=today.replace(day=1) - timedelta(days=95), set_by="Ananya Rao (demonstration)", set_at=utcnow(),
        confirmed_by=COMPLIANCE_OFFICER, confirmed_at=utcnow(), source="onboarding", notes=SOURCE)
    return 3
