"""Firm research & knowledge retrieval (spec §6.3).

Ingests documents into chunks + embeddings and retrieves cited context for agents, so a
recommendation reasons over the firm's own thinking. Vector search when embeddings are
available; lexical fallback otherwise."""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.llm.embeddings import embedding_service
from app.models.knowledge import ResearchChunk, ResearchDocument

log = get_logger("aurea.knowledge")


def _chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


async def ingest_document(session: AsyncSession, doc: ResearchDocument) -> int:
    """Chunk + embed a document. Returns number of chunks created."""
    # Clear old chunks (re-ingest).
    existing = (
        await session.execute(
            select(ResearchChunk).where(ResearchChunk.document_id == doc.id)
        )
    ).scalars().all()
    for c in existing:
        await session.delete(c)

    pieces = _chunk_text(f"{doc.title}\n\n{doc.body}")
    embeddings = embedding_service.embed(pieces) if pieces else []
    for idx, (text, emb) in enumerate(zip(pieces, embeddings)):
        session.add(
            ResearchChunk(
                firm_id=doc.firm_id,
                document_id=doc.id,
                chunk_index=idx,
                text=text,
                embedding=emb,
            )
        )
    await session.flush()
    log.info("research_ingested", doc=doc.title, chunks=len(pieces), embedded=embedding_service.available)
    return len(pieces)


async def retrieve(
    session: AsyncSession, firm_id: uuid.UUID, query: str, k: int = 4
) -> list[dict]:
    """Retrieve top-k research chunks with their document for citation."""
    query = (query or "").strip()
    if not query:
        return []

    results: list[tuple[ResearchChunk, float]] = []
    qvec = embedding_service.embed_one(query) if embedding_service.available else None

    if qvec is not None:
        rows = (
            await session.execute(
                select(ResearchChunk)
                .where(ResearchChunk.firm_id == firm_id, ResearchChunk.embedding.isnot(None))
                .order_by(ResearchChunk.embedding.cosine_distance(qvec))
                .limit(k)
            )
        ).scalars().all()
        results = [(c, 0.0) for c in rows]
    else:
        # Lexical fallback.
        terms = [t for t in query.lower().split() if len(t) > 3][:6]
        cond = or_(*[ResearchChunk.text.ilike(f"%{t}%") for t in terms]) if terms else None
        stmt = select(ResearchChunk).where(ResearchChunk.firm_id == firm_id)
        if cond is not None:
            stmt = stmt.where(cond)
        rows = (await session.execute(stmt.limit(k))).scalars().all()
        results = [(c, 0.0) for c in rows]

    doc_ids = list({c.document_id for c, _ in results})
    docs = {
        d.id: d
        for d in (
            await session.execute(
                select(ResearchDocument).where(ResearchDocument.id.in_(doc_ids))
            )
        ).scalars().all()
    } if doc_ids else {}

    out = []
    for chunk, _ in results:
        doc = docs.get(chunk.document_id)
        out.append(
            {
                "document_id": str(chunk.document_id),
                "title": doc.title if doc else "",
                "doc_type": doc.doc_type if doc else "",
                "author": doc.author if doc else None,
                "excerpt": chunk.text[:600],
            }
        )
    return out
