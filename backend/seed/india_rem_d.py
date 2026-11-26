"""The India remainders on the demonstration firm: a manager's own staff, a PMS client's unit values and a verifier's letter, and a minor's PAN
(B-303, B-283).

Every record is invented for the demonstration firm and says so in its evidence. Dates are days before the firm's today, so nothing ages. Run
from `seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before it; it adds nothing where a unit value
or a recorded post is on file. No fee line is debited here: a debit is a second person's step behind a Confirm card.

  Karthik Iyer      thirteen month-end values per unit for his PMS strategy, the units stated, a dip and a recovery so the high-water mark
                    matters; a verifier's letter (invented) that covers the strategy up to the quarter before the last one, so the last quarter
                    reads "not verified"
  Manager staff     Karthik Iyer recorded as an employee of a venture manager (invented): the AIF's lower minimum applies to that manager's
                    own products alone
  Ira Menon         a minor whose guardian (her father, already with a KYC record) is linked: her PAN is recorded from the document with the
                    guardian linked, so her papers read ready
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, manager_staff, pan, pms, pms_ledger
from app.core.security import UserRole
from app.models.graph import Mandate, Person
from app.models.india_rem_d import ManagerAffiliation, PmsUnitValue
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India remainders (demonstration)"
MANAGER = "Demonstration Venture Partners LLP (invented)"
NAVS = [100.0, 102.4, 104.1, 103.0, 106.8, 109.5, 108.2, 111.9, 113.4, 112.0, 115.2, 118.6, 117.3]
UNITS = 120_000.0
INVENTED_PAN = "DEMPI0001R"


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def month_ends(today: date, n: int) -> list[date]:
    """The last n month ends before today (a month end that is today is the day's own, so it is left to the next run), oldest first."""
    out: list[date] = []
    y, m = today.year, today.month
    while len(out) < n:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(_month_end(y, m))
    return sorted(out)


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(PmsUnitValue.id).where(PmsUnitValue.firm_id == firm.id).limit(1))).first():
        return 0
    if (await s.execute(select(ManagerAffiliation.id).where(ManagerAffiliation.firm_id == firm.id).limit(1))).first():
        return 0
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    karthik = people.get("Karthik Iyer")
    if karthik is None:
        return 0
    today = firm_time.today(firm)
    n = 0

    # A manager's own staff, from a document.
    await manager_staff.record(s, firm, UserRole.COMPLIANCE, by=BY, person_id=karthik.id, manager=MANAGER, affiliation="employee",
                               effective_on=today - timedelta(days=400), document=f"Appointment letter, filed ({SOURCE})")
    n += 1

    # A minor's PAN, recorded from the document with the guardian linked.
    ira = people.get("Ira Menon")
    if ira is not None and not ira.pan:
        try:
            await pan.record(s, firm, by=BY, person_id=ira.id, pan=INVENTED_PAN, source_document=f"PAN card scan, filed ({SOURCE})")
            n += 1
        except pan.PanError:
            pass

    # The PMS client's value per unit, month by month, from the manager's statement.
    mandate = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id == karthik.id))).scalars().first()
    if mandate is None or not pms.applies(firm):
        return n
    for day, nav in zip(month_ends(today, len(NAVS)), NAVS):
        await pms_ledger.record_unit_value(s, firm, UserRole.PORTFOLIO_TEAM, by=BY, mandate_id=mandate.id, as_of=day, nav_per_unit=nav, units=UNITS,
                                           document=f"The manager's monthly statement for {day.strftime('%B %Y')}, filed ({SOURCE})")
        n += 1

    # A verifier's letter that covers the strategy to the quarter before the last one: the last quarter reads "not verified".
    start, end, _ = pms.last_quarter(today)
    covered_to = start - timedelta(days=1)
    agreement = await pms.standing(s, firm.id, mandate.id)
    if agreement is not None and covered_to >= today - timedelta(days=400):
        await pms_ledger.record_verification(s, firm, UserRole.COMPLIANCE, by=BY, strategy=agreement.strategy, verifier="A performance verifier (invented)",
                                             period_start=agreement.signed_on, period_end=covered_to, letter_date=min(today, covered_to + timedelta(days=20)),
                                             scope="The strategy's time-weighted returns since the agreement, net of fees (invented)",
                                             document=f"The verifier's letter, filed ({SOURCE})")
        n += 1
    return n
