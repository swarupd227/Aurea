"""Top-up seed for Advise & Engage — more meetings and client reports across households.

Idempotent: safe to run repeatedly (skips rows already present by household+title). Run standalone
(`python -m seed.engage_extra`) against an existing DB without a reseed, and also called from run.py
so fresh installs are populated too."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.engagement import ClientReport, Meeting
from app.models.enums import MeetingStatus, ReportStatus
from app.models.graph import Household
from app.models.tenant import Firm

NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)  # deterministic; matches the demo "today"

# (title, status, days_from_now)
MEETINGS = [
    ("Annual strategy review", MeetingStatus.COMPLETED, -46),
    ("Quarterly portfolio review", MeetingStatus.PREPPED, 4),
    ("Goals & cashflow check-in", MeetingStatus.SCHEDULED, 11),
    ("Estate & next-gen planning", MeetingStatus.SCHEDULED, 25),
    ("Mid-year review", MeetingStatus.COMPLETED, -20),
    ("Market volatility catch-up", MeetingStatus.COMPLETED, -7),
    ("Portfolio rebalancing discussion", MeetingStatus.PREPPED, 6),
]

# (title, period, status)
REPORTS = [
    ("Q2 2026 Portfolio Commentary", "Q2 2026", ReportStatus.CLIENT_READY),
    ("Annual Wealth Review", "FY2025", ReportStatus.CLIENT_READY),
    ("Market Outlook & Positioning", "Jun 2026", ReportStatus.DRAFT),
    ("Retirement Readiness Summary", "Jun 2026", ReportStatus.DRAFT),
    ("Tax-Year Planning Note", "FY2026", ReportStatus.DRAFT),
    ("Values-Aligned Impact Report", "H1 2026", ReportStatus.CLIENT_READY),
]


def _notes() -> dict:
    return {
        "summary": ["Reviewed positioning against the house view and reaffirmed the long-term plan.",
                    "Client comfortable after we talked through the recent drawdown."],
        "sentiment": "positive",
        "action_items": ["Model a tax-efficient drawdown for next year",
                         "Send the updated cashflow plan", "Review the cash buffer level"],
        "proposed_goals": [],
    }


def _brief() -> dict:
    return {"agenda": ["Portfolio review & positioning vs the house view",
                       "Progress against goals — am I on track?", "Cash, liquidity & upcoming needs"],
            "watch_items": [], "goals": [], "house_views": [], "life_events": [],
            "portfolio": {"total_value": 0}}


def _sections(title: str, hh: str) -> list[dict]:
    return [
        {"heading": "Summary",
         "body": f"This {title.lower()} for {hh} reviews performance, positioning and the actions we are "
                 "taking, grounded in the firm's current house view."},
        {"heading": "Performance & attribution",
         "body": "The portfolio tracked its benchmark over the period; equities drove the majority of "
                 "return, partially offset by fixed income as rates moved."},
        {"heading": "Positioning & outlook",
         "body": "We remain at the strategic allocation with a modest quality tilt, watching inflation "
                 "and rate-path risk, and holding a cash buffer for resilience."},
        {"heading": "Recommended actions",
         "body": "Rebalance to target within the capital-gains budget, harvest available losses, and "
                 "review the cash level against upcoming liquidity needs."},
    ]


async def seed_engage_extra(session, firm) -> tuple[int, int]:
    households = (await session.execute(
        select(Household).where(Household.firm_id == firm.id).order_by(Household.created_at)
    )).scalars().all()
    if not households:
        return (0, 0)

    existing_m = {(m.household_id, m.title) for m in (await session.execute(
        select(Meeting).where(Meeting.firm_id == firm.id))).scalars().all()}
    existing_r = {(r.household_id, r.title) for r in (await session.execute(
        select(ClientReport).where(ClientReport.firm_id == firm.id))).scalars().all()}

    added_m = added_r = 0
    for i, (title, status, days) in enumerate(MEETINGS):
        hh = households[i % len(households)]
        full = f"{title} — {hh.name}"
        if (hh.id, full) in existing_m:
            continue
        m = Meeting(firm_id=firm.id, household_id=hh.id, title=full,
                    scheduled_at=NOW + timedelta(days=days), status=status)
        if status == MeetingStatus.COMPLETED:
            m.transcript = ("Adviser: Thanks for coming in — how are you feeling about things?\n"
                            "Client: Good, reassured after we talked through the plan.")
            m.notes = _notes()
            m.brief = _brief()
        elif status == MeetingStatus.PREPPED:
            m.brief = _brief()
        session.add(m)
        added_m += 1

    for i, (title, period, status) in enumerate(REPORTS):
        hh = households[i % len(households)]
        if (hh.id, title) in existing_r:
            continue
        r = ClientReport(firm_id=firm.id, household_id=hh.id, title=title, period=period,
                         status=status, sections=_sections(title, hh.name), data={})
        if status == ReportStatus.CLIENT_READY:
            r.published_at = NOW - timedelta(days=3)
        session.add(r)
        added_r += 1

    await session.flush()
    return (added_m, added_r)


async def _main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one()
        added_m, added_r = await seed_engage_extra(s, firm)
        await s.commit()
        print(f"engage_extra: added meetings={added_m} reports={added_r}")


if __name__ == "__main__":
    asyncio.run(_main())
