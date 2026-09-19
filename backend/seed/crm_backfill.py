"""Seed a starter CRM pipeline (L200-8 §2.1) for firms that predate these tables.

Idempotent like every other *_backfill.py: keyed on (firm, email) for contacts, safe to
re-run, and does nothing to a firm that already has CRM contacts.

    python -m seed.crm_backfill
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import UserRole
from app.models.identity import User
from app.models.tenant import Firm

log = get_logger("aurea.seed.crm_backfill")

_CONTACTS = [
    dict(full_name="Priya Nathan", email="priya.nathan@example.com", contact_type="prospect",
         source="referral", notes="Referred by the Hartmans; sold a business, exploring advisers."),
    dict(full_name="Marcus Webb", email="marcus.webb@example.com", contact_type="prospect",
         source="event", notes="Met at the firm's Q3 client event; mass-affluent, two young kids."),
    dict(full_name="Dana Okafor, CPA", email="dana.okafor@example-cpa.com", contact_type="centre_of_influence",
         source="cold_outreach", notes="Local CPA; sends rollover referrals a few times a year."),
]


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        created_contacts = created_opps = created_activity = 0

        for firm in firms:
            existing_emails = {
                c.email for c in (await s.execute(
                    select(CrmContact).where(CrmContact.firm_id == firm.id)
                )).scalars().all()
            }
            if existing_emails:
                continue  # this firm already has a pipeline; leave it alone

            owner = (await s.execute(
                select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER)
            )).scalars().first()

            now = utcnow()
            for i, spec in enumerate(_CONTACTS):
                contact = CrmContact(
                    firm_id=firm.id, owner_user_id=owner.id if owner else None,
                    is_active=True, **spec,
                )
                s.add(contact)
                await s.flush()
                created_contacts += 1

                if spec["contact_type"] != "prospect":
                    continue  # only prospects carry a pipeline deal

                stage = ("qualified", "lead")[i % 2]
                opp = CrmOpportunity(
                    firm_id=firm.id, contact_id=contact.id,
                    title=f"{contact.full_name.split(',')[0]} — new relationship",
                    stage=stage, estimated_aum=750_000 if i == 0 else 350_000,
                    probability_pct=40 if stage == "qualified" else 15,
                    opened_at=now - timedelta(days=20 - 5 * i),
                )
                s.add(opp)
                await s.flush()
                created_opps += 1

                s.add(CrmActivity(
                    firm_id=firm.id, contact_id=contact.id, opportunity_id=opp.id,
                    activity_type="meeting", occurred_at=now - timedelta(days=15 - 5 * i),
                    detail="Discovery call — goals, current adviser situation, next steps agreed.",
                    logged_by_user_id=owner.id if owner else None,
                ))
                created_activity += 1

            log.info("crm_seeded", firm=firm.slug, contacts=len(_CONTACTS))

        await s.commit()
        print(f"\nSeeded {created_contacts} contact(s), {created_opps} opportunity(ies), "
              f"{created_activity} activity row(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
