"""India row 9 on the demonstration firm: a consolidated account statement a person supplied.

The Account Aggregator itself is simulated and opt-in (`app/simulations/aa_feed`, switched on with AUREA_SIMULATIONS and
`python -m app.simulations seed aa_feed`), so it is not written here. What is written here is the real feature's other
way in: a CAMS / KFintech statement for Lakshmi Iyer, read into held-away lines from a document, as a person would. Every
record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm is built
and from `seed.india_demo_backfill` on a firm built before row 9; it adds nothing where the statement is already recorded.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import account_aggregator, firm_time
from app.models.aggregator import EcasStatement
from app.models.graph import Household, Person
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 9 (demonstration)"
HOUSEHOLD, PERSON = "The Iyer Family", "Lakshmi Iyer"
# The firm's own regular-plan funds, by their AMFI ISINs; units and values are invented.
LINES = """account,isin,name,units,value,institution
Folio 5550123/45,INF179K01DC2,HDFC Corporate Bond Fund - Regular Plan - Growth,24000,819360,HDFC Mutual Fund
Folio 5550987/12,INF789F01JN2,UTI Nifty 50 Index Fund - Regular Plan - Growth,3100,503967,UTI Mutual Fund
"""


async def add(s: AsyncSession, firm: Firm) -> int:
    household = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == HOUSEHOLD))).scalar_one_or_none()
    if household is None:
        return 0
    if (await s.execute(select(EcasStatement.id).where(EcasStatement.firm_id == firm.id, EcasStatement.household_id == household.id).limit(1))).first():
        return 0
    person = (await s.execute(select(Person).where(Person.household_id == household.id, Person.full_name == PERSON))).scalar_one_or_none()
    if person is None:
        return 0
    stated = firm_time.today(firm).replace(day=1) - timedelta(days=1)
    out = await account_aggregator.record_ecas(
        s, firm, by=BY, household_id=household.id, person_id=person.id, kind="cams_kfintech", statement_date=stated,
        document=f"CAMS and KFintech consolidated account statement for {PERSON}, filed in the client's folder ({SOURCE})", lines_text=LINES)
    return out["added"] + out["replaced"]
