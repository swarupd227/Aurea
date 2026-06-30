"""Firm research & knowledge (spec §6.3) — the firm's IP, made retrievable and cited.

Documents are chunked and embedded into pgvector so agents reason over — and cite — the
firm's own thinking rather than the open internet. Embeddings are optional: if the local
embedding model is unavailable, retrieval falls back to lexical search over the text."""
from __future__ import annotations

import uuid

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

EMBED_DIM = 384  # BAAI/bge-small-en-v1.5 via fastembed


class ResearchDocument(Base):
    """A house view, research note, model rationale or codified adviser playbook."""

    __tablename__ = "research_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(280))
    doc_type: Mapped[str] = mapped_column(String(48), default="house_view")  # house_view/research/playbook
    author: Mapped[str | None] = mapped_column(String(160))
    summary: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="published")  # draft/under_review/published
    published_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    chunks: Mapped[list["ResearchChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ResearchChunk(Base):
    """A retrievable chunk with its embedding."""

    __tablename__ = "research_chunk"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_document.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)

    document: Mapped["ResearchDocument"] = relationship(back_populates="chunks")
