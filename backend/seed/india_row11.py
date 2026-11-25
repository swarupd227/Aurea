"""India row 11 on the demonstration firm: what the tax engine reads from records a person made.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm is
built and from `seed.india_demo_backfill` on a firm built before row 11; it adds nothing where a loss is already recorded.

  Rajesh Sharma   a ledger of capital losses from returns: the Rs 40,000 long-term loss his profile already carried, now with its
                  year and its return (so the position is unchanged), one filed after its due day and one eight years old, which
                  carry nothing and say why.
  Meera Sharma    a salary and the old regime's deductions on file, so the new regime against the old is a finding and not a request.
  ITC             its price on 31 Jan 2018, as a person read it. No lot in the demonstration book is that old, so it changes no figure
                  here; it is what grandfathering reads when one is.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets, tax_india_full as full
from app.models.graph import Person
from app.models.india_tax import TaxLossEntry
from app.models.portfolio import Instrument
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 11 (demonstration)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(TaxLossEntry.id).where(TaxLossEntry.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    current = int(markets.tax_year(markets.MARKETS["IN"], today)["label"][:4])
    rajesh = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Rajesh Sharma"))).scalar_one_or_none()
    if rajesh is None:
        return 0
    written = 0
    # The loss his profile carried, now with its year and its return: the position reads the same.
    for year, kind, amount, filed, doc in (
            (current - 2, "long_term", 40_000, date(current - 1, 7, 28), "ITR-2 acknowledgement"),
            (current - 4, "short_term", 15_000, date(current - 3, 9, 15), "ITR-2 acknowledgement, filed after its due day"),
            (current - 9, "long_term", 10_000, date(current - 8, 7, 20), "ITR-2 acknowledgement")):
        await full.record_loss(s, firm, by=BY, person_id=rajesh.id, tax_year=full.year_label(year), kind=kind, amount=amount, return_filed_on=filed,
                               document=f"{doc} ({SOURCE})")
        written += 1
    meera = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Meera Sharma"))).scalar_one_or_none()
    if meera is not None:
        tax = dict((meera.profile or {}).get("tax") or {})
        tax.update({"salary_income": tax.get("annual_income") or 2_400_000, "old_regime_deductions": 350_000})
        meera.profile = {**(meera.profile or {}), "tax": tax}
        written += 1
    itc = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "ITC.NS"))).scalar_one_or_none()
    if itc is not None:
        await full.record_fmv(s, firm, by=BY, symbol="ITC.NS", fmv_per_unit=280.0,
                              document=f"Exchange price for 31 Jan 2018, as read by a person ({SOURCE})")
        written += 1
    return written
