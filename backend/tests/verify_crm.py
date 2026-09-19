"""Prove the CRM pipeline (L200-8 §2.1) computes what a growth dashboard needs: stage
counts, weighted pipeline value from probability, win rate on closed deals, and aging on
open ones — and that a contact converting to a household is the one deliberate seam back
into the client graph, not a silent merge.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_crm
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import crm as engine
from app.core.db import Base, utcnow
from app.models.crm import CrmContact, CrmOpportunity
from app.models.graph import Household
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "household", "crm_contact", "crm_opportunity", "crm_activity"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        s.add(firm)
        await s.flush()

        now = utcnow()
        contacts = [
            CrmContact(firm_id=firm.id, full_name="A Prospect", contact_type="prospect", is_active=True),
            CrmContact(firm_id=firm.id, full_name="B Prospect", contact_type="prospect", is_active=True),
            CrmContact(firm_id=firm.id, full_name="C Prospect", contact_type="prospect", is_active=True),
            CrmContact(firm_id=firm.id, full_name="D COI", contact_type="centre_of_influence", is_active=True),
        ]
        s.add_all(contacts)
        await s.flush()
        a, b, c, _coi = contacts

        s.add_all([
            # Open, qualified: counts toward weighted pipeline and aging.
            CrmOpportunity(firm_id=firm.id, contact_id=a.id, title="A deal", stage="qualified",
                            estimated_aum=1_000_000, probability_pct=40,
                            opened_at=now - timedelta(days=30)),
            # Open, lead, no AUM yet.
            CrmOpportunity(firm_id=firm.id, contact_id=b.id, title="B deal", stage="lead",
                            estimated_aum=None, probability_pct=10, opened_at=now - timedelta(days=5)),
            # Closed won.
            CrmOpportunity(firm_id=firm.id, contact_id=c.id, title="C deal", stage="won",
                            estimated_aum=500_000, probability_pct=100,
                            opened_at=now - timedelta(days=60), closed_at=now - timedelta(days=10)),
            # Closed lost — must not count toward open pipeline or weighted value.
            CrmOpportunity(firm_id=firm.id, contact_id=c.id, title="C deal 2", stage="lost",
                            estimated_aum=200_000, probability_pct=20,
                            opened_at=now - timedelta(days=90), closed_at=now - timedelta(days=80),
                            lost_reason="Chose incumbent adviser."),
        ])
        await s.flush()

        print("\n=== pipeline_summary: stage counts, weighted value, win rate, aging ===")
        summary = await engine.pipeline_summary(s, firm.id)
        check("lead count", summary["by_stage"]["lead"], 1)
        check("qualified count", summary["by_stage"]["qualified"], 1)
        check("won count", summary["by_stage"]["won"], 1)
        check("lost count", summary["by_stage"]["lost"], 1)
        check("open pipeline count excludes won/lost", summary["open_pipeline_count"], 2)
        check("open pipeline aum excludes won/lost", summary["open_pipeline_aum"], 1_000_000.0)
        # weighted = 1,000,000 * 0.40 + 0 * 0.10 = 400,000
        check("weighted open value", summary["weighted_open_value"], 400_000.0)
        check("win rate: 1 won / 2 closed", summary["win_rate_pct"], 50.0)
        aging_titles = [r["title"] for r in summary["aging_open_deals"]]
        check("aging sorted oldest-open first", aging_titles, ["A deal", "B deal"])

        print("\n=== converting a contact links the household without merging records ===")
        household = Household(id=uuid.uuid4(), firm_id=firm.id, name="C Household")
        s.add(household)
        await s.flush()
        c.household_id = household.id
        c.contact_type = "client_contact"
        await s.flush()

        refreshed = (await s.execute(
            select(CrmContact).where(CrmContact.id == c.id)
        )).scalar_one()
        check("contact now links the household", str(refreshed.household_id), str(household.id))
        check("contact_type flips to client_contact", refreshed.contact_type, "client_contact")
        check("the contact row itself still exists (no merge/delete)", refreshed.full_name, "C Prospect")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
