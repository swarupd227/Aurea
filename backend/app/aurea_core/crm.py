"""CRM pipeline analytics (L200-8 §2.1) — the numbers a growth/practice-management view
would show: stage counts, weighted pipeline value, win rate, and aging on open deals."""
from __future__ import annotations

import uuid
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.crm import OPEN_STAGES, PIPELINE_STAGES, CrmOpportunity


def _aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def pipeline_summary(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    opps = (await session.execute(
        select(CrmOpportunity).where(CrmOpportunity.firm_id == firm_id)
    )).scalars().all()

    now = utcnow()
    by_stage = {stage: 0 for stage in PIPELINE_STAGES}
    aum_by_stage = {stage: 0.0 for stage in PIPELINE_STAGES}
    weighted_open_value = 0.0
    aging: list[dict] = []

    for o in opps:
        by_stage[o.stage] = by_stage.get(o.stage, 0) + 1
        aum = float(o.estimated_aum or 0)
        aum_by_stage[o.stage] = aum_by_stage.get(o.stage, 0.0) + aum
        if o.stage in OPEN_STAGES:
            weighted_open_value += aum * (o.probability_pct or 0) / 100
            opened = _aware(o.opened_at)
            if opened is not None:
                aging.append({
                    "opportunity_id": str(o.id), "title": o.title, "stage": o.stage,
                    "days_open": (now - opened).days,
                })

    won = by_stage.get("won", 0)
    lost = by_stage.get("lost", 0)
    closed = won + lost

    return {
        "by_stage": by_stage,
        "aum_by_stage": {k: round(v, 2) for k, v in aum_by_stage.items()},
        "open_pipeline_count": sum(by_stage.get(s, 0) for s in OPEN_STAGES),
        "open_pipeline_aum": round(sum(aum_by_stage.get(s, 0.0) for s in OPEN_STAGES), 2),
        "weighted_open_value": round(weighted_open_value, 2),
        "win_rate_pct": round(100 * won / closed, 1) if closed else None,
        "aging_open_deals": sorted(aging, key=lambda r: -r["days_open"]),
    }
