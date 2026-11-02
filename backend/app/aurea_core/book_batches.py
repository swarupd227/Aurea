"""Acquired books waiting to be mapped into the firm's client records.

A batch holds an inbound feed; the book integration agent maps its clients and securities and flags
conflicts, and its proposal is what commits them. No file import is built: a new batch loads the sample
book (`sample_book.py`) and is marked as one, so nothing generated passes for a real acquisition.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AgentKey
from app.models.governance import Recommendation
from app.models.onboarding import BookIntegrationBatch


class BatchError(ValueError):
    """A batch that cannot be read or started as asked, with the reason."""


def as_dict(b: BookIntegrationBatch) -> dict:
    return {
        "id": str(b.id), "source_firm": b.source_firm, "status": b.status,
        "sample": bool((b.feed or {}).get("sample")),
        "feed": b.feed, "mappings": b.mappings, "stats": b.stats, "committed": b.committed,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


async def list_for(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    rows = (await session.execute(select(BookIntegrationBatch).where(BookIntegrationBatch.firm_id == firm_id)
                                  .order_by(BookIntegrationBatch.created_at.desc()))).scalars().all()
    return [as_dict(b) for b in rows]


async def get(session: AsyncSession, firm_id: uuid.UUID, batch_id: uuid.UUID) -> BookIntegrationBatch:
    b = await session.get(BookIntegrationBatch, batch_id)
    if b is None or b.firm_id != firm_id:
        raise BatchError("Batch not found.")
    return b


async def one(session: AsyncSession, firm_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    """A batch with the proposal its latest run raised."""
    b = await get(session, firm_id, batch_id)
    rec = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.subject_id == b.id,
                                     Recommendation.agent_key == AgentKey.BOOK_INTEGRATION)
        .order_by(Recommendation.created_at.desc()).limit(1))).scalar_one_or_none()
    return {**as_dict(b), "recommendation_id": str(rec.id) if rec else None,
            "recommendation_status": str(rec.status.value if hasattr(rec.status, "value") else rec.status) if rec else None}


async def start(session: AsyncSession, firm_id: uuid.UUID, source_firm: str, feed: dict | None = None) -> BookIntegrationBatch:
    from app.aurea_core.sample_book import sample_feed

    name = (source_firm or "").strip()
    if not name:
        raise BatchError("Name the firm the book comes from.")
    if feed is None:
        feed = {**sample_feed(name), "sample": True}
    b = BookIntegrationBatch(firm_id=firm_id, source_firm=name, feed=feed)
    session.add(b)
    await session.flush()
    return b
