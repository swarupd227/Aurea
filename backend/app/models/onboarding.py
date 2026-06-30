"""Acquire & onboard models (spec §7 'acquire & onboard', Table 8).

OnboardingCase tracks a prospect from intake → screening → compliance review → materialisation
into the client brain. BookIntegrationBatch tracks an acquired book from inbound feed →
reconciliation → commit as golden records."""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import BookBatchStatus, ClientSegment, OnboardingStatus


class OnboardingCase(Base):
    __tablename__ = "onboarding_case"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    prospect_name: Mapped[str] = mapped_column(String(200))
    is_entity: Mapped[bool] = mapped_column(default=False)  # individual vs trust/foundation
    entity_type: Mapped[str | None] = mapped_column(String(24))
    segment: Mapped[ClientSegment] = mapped_column(String(32), default=ClientSegment.PRIVATE_WEALTH)
    status: Mapped[OnboardingStatus] = mapped_column(String(16), default=OnboardingStatus.INTAKE, index=True)

    # Intake: contact, objectives, risk answers, source-of-wealth, associated parties.
    intake: Mapped[dict] = mapped_column(JSON, default=dict)
    # AML/CFT screening summary (set during sense()).
    screening: Mapped[dict] = mapped_column(JSON, default=dict)
    # Agent proposal: suitability + recommended mandate set-up.
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    # Exceptions requiring a human decision.
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    # References to materialised graph nodes once approved.
    materialized: Mapped[dict] = mapped_column(JSON, default=dict)
    sla_days: Mapped[int] = mapped_column(Integer, default=30)
    notes: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list["OnboardingDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class OnboardingDocument(Base):
    __tablename__ = "onboarding_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[str] = mapped_column(String(48))
    filename: Mapped[str] = mapped_column(String(200))
    # The retained source (synthetic document text) — kept for verification (spec §6.4).
    raw_text: Mapped[str] = mapped_column(Text)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    field_confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    verified: Mapped[bool] = mapped_column(default=False)

    case: Mapped["OnboardingCase"] = relationship(back_populates="documents")


class BookIntegrationBatch(Base):
    __tablename__ = "book_integration_batch"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    source_firm: Mapped[str] = mapped_column(String(200))
    status: Mapped[BookBatchStatus] = mapped_column(String(16), default=BookBatchStatus.RECEIVED, index=True)
    # Raw inbound feed (clients, accounts, holdings, capital-call notices).
    feed: Mapped[dict] = mapped_column(JSON, default=dict)
    # Reconciliation output: per-record mappings + conflicts (set during think()).
    mappings: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    committed: Mapped[dict] = mapped_column(JSON, default=dict)
