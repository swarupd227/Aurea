"""Analytics API — the five analytics layers over the Unified Client Brain (Companion §2)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core.analytics import (
    adoption, advice, client_intelligence, overview, portfolio, practice, risk_data,
)
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(staff_user)])


@router.get("/overview")
async def get_overview(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Everything in one governed read: headline KPIs, the maturity climb, and all five layers."""
    return await overview.compute_all(db, firm.id)


@router.get("/client-intelligence")
async def get_client_intelligence(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await client_intelligence.compute(db, firm.id)


@router.get("/portfolio")
async def get_portfolio(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await portfolio.compute(db, firm.id)


@router.get("/advice")
async def get_advice(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await advice.compute(db, firm.id)


@router.get("/practice")
async def get_practice(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await practice.compute(db, firm.id)


@router.get("/risk-data")
async def get_risk_data(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await risk_data.compute(db, firm.id)


@router.get("/adoption")
async def get_adoption(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Layer 2.6 — Agent adoption, feature uptake, per-adviser engagement, and ROI metrics."""
    return await adoption.compute(db, firm.id)


@router.get("/maturity")
async def get_maturity():
    return {"maturity": overview.MATURITY, "layers": overview.LAYER_META}


@router.get("/scorecards")
async def get_scorecards(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Per-adviser scorecard: AUM, household count, recommendation throughput, meetings held."""
    from app.models.graph import Household, RelationshipEdge
    from app.models.governance import Recommendation
    from app.models.engagement import Meeting
    from app.models.enums import RecommendationStatus

    # All adviser users for this firm.
    advisers = (await db.execute(
        select(User).where(User.firm_id == firm.id, User.role == "adviser", User.is_active.is_(True))
    )).scalars().all()

    scorecards = []
    for adv in advisers:
        # Households linked via 'adviser' relationship edge (edges go to person/entity).
        from app.models.graph import LegalEntity, Person
        edges = (await db.execute(
            select(RelationshipEdge).where(
                RelationshipEdge.firm_id == firm.id,
                RelationshipEdge.kind == "adviser",
                RelationshipEdge.from_id == adv.id,
                RelationshipEdge.from_type == "user",
            )
        )).scalars().all()
        person_ids = [e.to_id for e in edges if e.to_type == "person"]
        entity_ids = [e.to_id for e in edges if e.to_type == "entity"]
        household_id_set: set[uuid.UUID] = set()
        if person_ids:
            rows = (await db.execute(
                select(Person.household_id).where(Person.id.in_(person_ids), Person.household_id.isnot(None))
            )).scalars().all()
            household_id_set.update(rows)
        if entity_ids:
            rows2 = (await db.execute(
                select(LegalEntity.household_id).where(LegalEntity.id.in_(entity_ids), LegalEntity.household_id.isnot(None))
            )).scalars().all()
            household_id_set.update(rows2)
        household_ids = list(household_id_set)
        hh_count = len(household_ids)

        # AUM: sum of holding market values via Account → Mandate → person/entity.
        from sqlalchemy import or_ as _or
        from app.models.graph import Account, Mandate
        from app.models.portfolio import Holding
        aum = 0.0
        if person_ids or entity_ids:
            clauses = []
            if person_ids:
                clauses.append(Mandate.person_id.in_(person_ids))
            if entity_ids:
                clauses.append(Mandate.entity_id.in_(entity_ids))
            aum_row = (await db.execute(
                select(func.coalesce(func.sum(Holding.market_value), 0))
                .join(Account, Account.id == Holding.account_id)
                .join(Mandate, Mandate.id == Account.mandate_id)
                .where(_or(*clauses))
            )).scalar_one()
            aum = float(aum_row)

        # Recommendations approved in last 90 days.
        from datetime import timedelta
        from app.core.db import utcnow
        since = utcnow() - timedelta(days=90)
        approved_count = (await db.execute(
            select(func.count(Recommendation.id)).where(
                Recommendation.firm_id == firm.id,
                Recommendation.status == RecommendationStatus.APPROVED,
                Recommendation.updated_at >= since,
            )
        )).scalar_one()

        # Meetings in last 90 days for these households.
        meeting_count = 0
        if household_ids:
            meeting_count = (await db.execute(
                select(func.count(Meeting.id)).where(
                    Meeting.firm_id == firm.id,
                    Meeting.household_id.in_(household_ids),
                    Meeting.scheduled_at >= since,
                )
            )).scalar_one()

        scorecards.append({
            "adviser_id": str(adv.id),
            "adviser_name": adv.full_name,
            "adviser_email": adv.email,
            "household_count": hh_count,
            "aum": aum,
            "recs_approved_90d": approved_count,
            "meetings_90d": meeting_count,
        })

    scorecards.sort(key=lambda x: x["aum"], reverse=True)
    return {"scorecards": scorecards}
