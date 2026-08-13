"""Acquire & onboard models (spec §7 'acquire & onboard', Table 8).

OnboardingCase tracks a prospect from intake → screening → compliance review → materialisation
into the client brain. BookIntegrationBatch tracks an acquired book from inbound feed →
reconciliation → commit as golden records."""
from __future__ import annotations

import uuid

import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import BookBatchStatus, ClientSegment, OnboardingStatus


class OnboardingCase(Base):
    __tablename__ = "onboarding_case"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    prospect_name: Mapped[str] = mapped_column(String(200))
    is_entity: Mapped[bool] = mapped_column(default=False)
    entity_type: Mapped[str | None] = mapped_column(String(24))
    # L200: structured registration type (Individual, IRA, Trust, LLC, etc.)
    registration_type: Mapped[str | None] = mapped_column(String(32))
    segment: Mapped[ClientSegment] = mapped_column(String(32), default=ClientSegment.PRIVATE_WEALTH)
    status: Mapped[OnboardingStatus] = mapped_column(String(16), default=OnboardingStatus.INTAKE, index=True)

    # Intake: contact, objectives, risk answers, source-of-wealth, associated parties.
    intake: Mapped[dict] = mapped_column(JSON, default=dict)
    # AML/CFT screening summary (set during sense()).
    screening: Mapped[dict] = mapped_column(JSON, default=dict)
    # L200: per-party screening disposition log [{party, result, score, disposition_note, screened_at}]
    screening_log: Mapped[list] = mapped_column(JSON, default=list)
    # Agent proposal: suitability + recommended mandate set-up.
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    # Exceptions requiring a human decision.
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    # References to materialised graph nodes once approved.
    materialized: Mapped[dict] = mapped_column(JSON, default=dict)
    sla_days: Mapped[int] = mapped_column(Integer, default=30)
    # NIGO: Not In Good Order
    nigo_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    nigo_reason: Mapped[str | None] = mapped_column(Text)
    # L200: structured NIGO root-cause taxonomy
    nigo_root_cause: Mapped[str | None] = mapped_column(String(32))
    # L200: AML customer risk rating (low/medium/high)
    aml_risk_tier: Mapped[str | None] = mapped_column(String(16))
    aml_risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # L200: CDD vs EDD depth (none/cdd/edd_pending/edd_complete)
    edd_status: Mapped[str | None] = mapped_column(String(16))
    # L200: DOL PTE 2020-02 rollover rationale status (pending/generated/reviewed)
    pte_status: Mapped[str | None] = mapped_column(String(16))
    # L200: pre-submission readiness score 0–100 from NIGO Prevention agent
    readiness_score: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    # Phase 3: CIP identity verification (via pluggable IdentityAdapter)
    cip_status: Mapped[str | None] = mapped_column(String(16))       # verified|review|failed
    cip_score: Mapped[float | None] = mapped_column(Numeric(4, 2))
    cip_flags: Mapped[list] = mapped_column(JSON, default=list)
    cip_reference_id: Mapped[str | None] = mapped_column(String(64)) # provider audit ref
    # Phase 3: Custodian single-keying (via pluggable CustodianAdapter)
    custodian_name: Mapped[str | None] = mapped_column(String(32))
    custodian_account_id: Mapped[str | None] = mapped_column(String(64))
    custodian_push_status: Mapped[str | None] = mapped_column(String(16))
    custodian_push_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["OnboardingDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    beneficial_owners: Mapped[list["BeneficialOwner"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    transfers: Mapped[list["TransferRequest"]] = relationship(
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


class BeneficialOwner(Base):
    """CDD Rule (31 CFR 1010.230) — 25%+ owners + control person for entity accounts."""
    __tablename__ = "beneficial_owner"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True
    )
    legal_name: Mapped[str] = mapped_column(String(200))
    dob: Mapped[str | None] = mapped_column(String(16))       # ISO date YYYY-MM-DD
    address: Mapped[str | None] = mapped_column(Text)
    id_number: Mapped[str | None] = mapped_column(String(64)) # passport / SSN (encrypted in prod)
    ownership_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    is_control_person: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    case: Mapped["OnboardingCase"] = relationship(back_populates="beneficial_owners")


class TransferRequest(Base):
    """F4 — ACAT / wire / ACH transfer tracking.

    Tracks a funds or securities transfer associated with an onboarding case.
    Status advances through a canonical state machine; real status updates come
    via provider webhooks or polling (mocked via advance_status endpoint).
    """
    __tablename__ = "transfer_request"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True
    )
    transfer_type: Mapped[str] = mapped_column(String(16))    # acat | wire | ach
    direction: Mapped[str] = mapped_column(String(8))         # in | out
    amount: Mapped[float | None] = mapped_column(Numeric(18, 2))
    asset_description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="initiated", index=True)
    provider: Mapped[str | None] = mapped_column(String(32))  # which adapter was used
    provider_ref: Mapped[str | None] = mapped_column(String(128))  # provider reference id
    custodian: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    initiated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped["OnboardingCase"] = relationship(back_populates="transfers")


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
