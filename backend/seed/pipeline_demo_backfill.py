"""Give the demonstration firm a pipeline worth demonstrating (B-159).

The demo firm (slug "demo") had two prospects and a CPA, so the funnel from lead to invested had nothing to show. This
adds invented prospects across every stage, in the same spirit as the firm's invented clients: some recently worked,
some gone quiet past their stage's target, two lost with their reasons, and two won whose onboarding cases are the
firm's seeded ones of the same name (The Ashworth Family, The Brennan Family), so a won deal shows where its
onboarding stands. Nothing about a client, a case or a household is changed: only pipeline rows are added, and a won
deal points at its case.

Only the firm whose slug is "demo"; idempotent by contact name, so a rerun adds nothing it already has.

    python -m seed.pipeline_demo_backfill
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
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

log = get_logger("aurea.seed.pipeline_demo_backfill")

# name, source, notes, email, stage, opened days ago, estimate, probability, [(days ago, type, detail)], lost reason, case
_DEALS = [
    ("Hemi Walker", "referral", "Referred by the Chen family; recently inherited a farm and its proceeds.", "hemi.walker@example.com",
     "lead", 9, None, None, [(6, "call", "Intro call: inheritance settling in the new year, wants to understand options")], None, None),
    ("Grace Liu", "event", "Met at the firm's retirement seminar; a GP planning to stop practising in three years.", None,
     "lead", 34, 600_000, None, [(29, "email", "Sent the seminar slides and asked for a call")], None, None),
    ("Rohan Mehta", "centre_of_influence", "Referred by Dana Okafor, CPA: rolling over a KiwiSaver and a UK pension.", "rohan.mehta@example.com",
     "qualified", 26, 1_100_000, 50, [(24, "meeting", "Discovery meeting: two pension transfers, wants one adviser for both"),
                                      (8, "email", "Sent the transfer authorities to sign")], None, None),
    ("Olivia Harris", "inbound", "Came through the website after selling her café.", "olivia.harris@example.com",
     "qualified", 41, 450_000, None, [(38, "call", "Qualifying call: sale proceeds in a term deposit, no adviser")], None, None),
    ("Sam & Aroha Ngata", "referral", "Referred by the Aroha Whānau Trust; both teachers, saving for a first home and retirement.", None,
     "proposal", 19, 380_000, 70, [(17, "meeting", "Discovery meeting with both"), (4, "email", "Sent the proposal and fee schedule")], None, None),
    ("James Whitaker", "event", "Business owner met at the chamber of commerce lunch; succession in five years.", "james.whitaker@example.com",
     "proposal", 52, 2_400_000, 40, [(47, "meeting", "Discovery meeting: business sale and family trust"),
                                     (21, "email", "Sent the proposal; he will discuss with his accountant")], None, None),
    ("Mereana Tane", "inbound", "Enquired about ethical investing for a charitable trust.", None,
     "lost", 70, 300_000, None, [(66, "call", "Qualifying call")], "Chose a specialist ethical-fund manager on fees", None),
    ("Peter Novak", "cold_outreach", "Follow-up from a direct-mail campaign.", None,
     "lost", 90, None, None, [(85, "call", "Not ready to move from his bank's adviser")], "Staying with his bank's adviser", None),
    ("The Ashworth Family", "referral", "Referred by an existing client; consolidating three accounts.", None,
     "won", 48, 900_000, 80, [(44, "meeting", "Discovery meeting"), (30, "meeting", "Proposal accepted")], None, "The Ashworth Family"),
    ("The Brennan Family", "centre_of_influence", "Referred by their solicitor during an estate settlement.", None,
     "won", 60, 650_000, 75, [(55, "meeting", "Discovery meeting"), (35, "email", "Signed engagement letter returned")], None, "The Brennan Family"),
]


async def backfill() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if firm is None:
            print("No demo firm; nothing to do.")
            return
        owner = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER)
                                 .order_by(User.created_at))).scalars().first()
        have = {c.full_name for c in (await s.execute(select(CrmContact).where(CrmContact.firm_id == firm.id))).scalars().all()}
        now = utcnow()
        added = linked = 0
        for name, source, notes, email, stage, opened, aum, prob, acts, lost, case_name in _DEALS:
            if name in have:
                continue
            case = None
            if case_name:
                case = (await s.execute(select(OnboardingCase).where(
                    OnboardingCase.firm_id == firm.id, OnboardingCase.prospect_name == case_name))).scalars().first()
                if case is None:
                    log.info("pipeline_demo_skip", name=name, reason="no seeded case of that name")
                    continue
                taken = (await s.execute(select(CrmOpportunity).where(CrmOpportunity.onboarding_case_id == case.id))).scalars().first()
                if taken is not None:
                    continue
            opened_at = now - timedelta(days=opened)
            contact = CrmContact(firm_id=firm.id, full_name=name, email=email, contact_type="prospect", source=source,
                                 notes=notes, owner_user_id=owner.id if owner else None, is_active=True, created_at=opened_at)
            s.add(contact)
            await s.flush()
            closed = None
            if stage in ("won", "lost"):
                closed = now - timedelta(days=min(d for d, _, _ in acts)) if acts else now
            opp = CrmOpportunity(firm_id=firm.id, contact_id=contact.id, title=f"{name} - new relationship", stage=stage,
                                 estimated_aum=aum, probability_pct=prob, opened_at=opened_at, closed_at=closed,
                                 lost_reason=lost, onboarding_case_id=case.id if case else None,
                                 converted_household_id=None)
            if case is not None and (case.materialized or {}).get("household_id"):
                import uuid
                opp.converted_household_id = uuid.UUID(str(case.materialized["household_id"]))
                contact.household_id, contact.contact_type = opp.converted_household_id, "client_contact"
            s.add(opp)
            await s.flush()
            for days, kind, detail in acts:
                s.add(CrmActivity(firm_id=firm.id, contact_id=contact.id, opportunity_id=opp.id, activity_type=kind,
                                  occurred_at=now - timedelta(days=days), detail=detail,
                                  logged_by_user_id=owner.id if owner else None))
            added += 1
            linked += 1 if case else 0
        await s.commit()
        print(f"\nDemo pipeline: {added} prospect(s) added, {linked} won deal(s) pointed at their onboarding case.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
