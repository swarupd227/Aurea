"""Give the demo book fee arrangements to bill against (L200 §2.1 Track A step 2).

An already-seeded firm has mandates but no `FeeArrangement` - arrangements are created at
onboarding activation, and these mandates were seeded directly. Without one a mandate
cannot be billed, so each household here gets the library schedule for its segment, set by
the adviser and confirmed by compliance (a different person, as maker/checker requires).

These are marked `source="seed"`: they are demo terms, not an agreement a client signed,
and nothing downstream treats them as one. Two households are deliberately left without an
arrangement so the billing run's "no agreement on file" exception has something real to
show, and one starts mid-quarter so proration does.

Schedules are chosen so the effective rate is plausible on the household's balance - the
firm's flat institutional minimum and the for-purpose retainer would charge a $330k
foundation more than 5%, which `fees.validate` rightly calls a data-entry error.

Idempotent: skips any mandate that already has an active arrangement.

    python -m seed.billing_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.aurea_core import billing
from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.billing import FeeArrangement
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.onboarding import FeeSchedule
from app.models.tenant import Firm

log = get_logger("aurea.seed.billing_backfill")

_LEAVE_UNAGREED = {"The Nguyen Family", "Tanaka Holdings"}
_MID_QUARTER = {"Whetu Trust": date(2026, 8, 15)}
_DEFAULT_START = date(2026, 1, 1)


async def backfill() -> None:
    async with SessionLocal() as s:
        created = skipped = 0
        for firm in (await s.execute(select(Firm))).scalars().all():
            schedules = {f.code: f for f in (await s.execute(
                select(FeeSchedule).where(FeeSchedule.firm_id == firm.id))).scalars().all()}
            if "PW-TIERED" not in schedules or "MA-FLAT" not in schedules:
                log.info("billing_backfill_no_schedules", firm=firm.slug)
                continue
            households = {h.id: h for h in (await s.execute(
                select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            people = {p.id: p.household_id for p in (await s.execute(
                select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            entities = {e.id: e.household_id for e in (await s.execute(
                select(LegalEntity).where(LegalEntity.firm_id == firm.id))).scalars().all()}
            has_arrangement = {a.mandate_id for a in (await s.execute(
                select(FeeArrangement).where(FeeArrangement.firm_id == firm.id,
                                             FeeArrangement.status == "active"))).scalars().all()}
            now = utcnow()

            for m in (await s.execute(select(Mandate).where(
                    Mandate.firm_id == firm.id, Mandate.is_active.is_(True)))).scalars().all():
                if m.id in has_arrangement:
                    continue
                hh = households.get(people.get(m.person_id) if m.person_id else entities.get(m.entity_id))
                if hh is None or hh.name in _LEAVE_UNAGREED:
                    skipped += 1
                    continue
                segment = getattr(hh.segment, "value", str(hh.segment))
                schedule = schedules["MA-FLAT" if segment == "mass_affluent" else "PW-TIERED"]
                await billing.activate_arrangement(
                    s, firm_id=firm.id, mandate=m, fee_schedule_id=schedule.id,
                    billing_method="arrears", billing_frequency="quarterly",
                    householding=segment == "private_wealth",
                    effective_from=_MID_QUARTER.get(hh.name, _DEFAULT_START),
                    set_by="sophie.adviser@aurea.demo", set_at=now - timedelta(days=30),
                    confirmed_by="compliance@aurea.demo", confirmed_at=now - timedelta(days=29),
                    source="seed", notes="Demo terms seeded for the demonstration book.",
                )
                created += 1
                log.info("billing_arrangement_seeded", household=hh.name, schedule=schedule.code)

        await s.commit()
        print(f"\nSeeded {created} fee arrangement(s); {skipped} mandate(s) deliberately left without one.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
