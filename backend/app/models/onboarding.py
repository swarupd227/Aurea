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

    # L200 Track A step 2 — fee schedule, billing method and householding, under
    # maker/checker. The confirming user must differ from the one who set it, which is the
    # dual-control L200 prescribes against mis-set fees.
    fee_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fee_schedule.id", ondelete="SET NULL"), nullable=True
    )
    billing_method: Mapped[str | None] = mapped_column(String(16))     # advance | arrears
    billing_frequency: Mapped[str | None] = mapped_column(String(16))  # monthly | quarterly | annually
    # Aggregate related accounts to reach a lower breakpoint.
    householding: Mapped[bool] = mapped_column(Boolean, default=False)
    billable_aum: Mapped[float | None] = mapped_column(Numeric(18, 2))
    fee_set_by: Mapped[str | None] = mapped_column(String(200))        # maker
    fee_set_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    fee_confirmed_by: Mapped[str | None] = mapped_column(String(200))  # checker
    fee_confirmed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # When the case was actually activated (materialised into the client brain). Every
    # other milestone is already timestamped on its own evidence — disclosures carry
    # delivered_at, parties screened_at, transfers settled_at — so this is the only event
    # cycle-time metrics need that nothing else records.
    activated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["OnboardingDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    beneficial_owners: Mapped[list["BeneficialOwner"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    disclosures: Mapped[list["DisclosureDelivery"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    parties: Mapped[list["OnboardingParty"]] = relationship(
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


class FeeSchedule(Base):
    """A firm's published fee schedule (L200 §2.1 Track A, step 2).

    L200 controls the "fee schedule mis-set at onboarding" failure mode with a
    "fee-schedule library with maker/checker" — so the schedule is a firm-level catalogue
    selected from, not free text typed per case. Errors made here surface much later as
    billing exceptions and client reimbursements.
    """
    __tablename__ = "fee_schedule"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # tiered_bps | flat_bps | flat_fee
    fee_type: Mapped[str] = mapped_column(String(16), default="tiered_bps")
    # Tiered breakpoints: [{"min_aum": 0, "max_aum": 1000000, "bps": 100}, ...]
    tiers: Mapped[list] = mapped_column(JSON, default=list)
    flat_bps: Mapped[float | None] = mapped_column(Numeric(6, 2))
    flat_fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    minimum_annual_fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="NZD")
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DisclosureDelivery(Base):
    """Evidence that a required disclosure was delivered to the prospect.

    L200 §5 names this as the control for its first failure mode — "disclosure not
    delivered/evidenced -> exam deficiency; rescission risk", controlled by
    "system-enforced delivery gates before account activation; delivery logs".

    The point of the record is the evidence, not the tick: regulators expect to see *when*
    a document was delivered, *how*, and *by whom*, and annual redelivery obligations run
    from that timestamp. A boolean would not survive an exam.
    """
    __tablename__ = "disclosure_delivery"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[str] = mapped_column(String(48), index=True)   # form_adv_2a, form_crs, ...
    delivered_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    method: Mapped[str] = mapped_column(String(16))                 # email|portal|in_person|post
    # Provider-side handle for the delivery — e-sign envelope id, message id, upload ref.
    evidence_ref: Mapped[str | None] = mapped_column(String(128))
    delivered_by: Mapped[str | None] = mapped_column(String(200))   # actor label
    # Client acknowledgement, where the channel supports it (portal open, e-sign complete).
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    case: Mapped["OnboardingCase"] = relationship(back_populates="disclosures")


class OnboardingParty(Base):
    """Any person or entity holding a role on the account.

    The single party model for a case. L200's control for the sanctions-breach failure
    mode is "party-model completeness checks: every role on the account must have a
    screened identity record" — impossible to perform when only beneficial owners are
    modelled, which is why joint owners, trustees and POA holders were never screened.

    Beneficial owners are a *role* here, not a separate table: `BeneficialOwner` rows are
    backfilled into this model with role='beneficial_owner'.
    """
    __tablename__ = "onboarding_party"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_case.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), index=True)
    legal_name: Mapped[str] = mapped_column(String(200))

    # Identity — the CIP data set (name, DOB, address, ID number).
    dob: Mapped[str | None] = mapped_column(String(16))           # ISO date YYYY-MM-DD
    address: Mapped[str | None] = mapped_column(Text)
    id_number: Mapped[str | None] = mapped_column(String(64))     # encrypted at rest in prod
    id_type: Mapped[str | None] = mapped_column(String(24))       # passport | licence | ssn | ein

    # Ownership — only meaningful for beneficial owners under the CDD Rule.
    ownership_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    is_control_person: Mapped[bool] = mapped_column(Boolean, default=False)

    # Per-party CIP outcome, so identity verification is tracked per person rather than
    # once for the case.
    cip_status: Mapped[str | None] = mapped_column(String(16))    # verified|review|failed
    cip_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-party screening outcome — what the completeness gate reads.
    screening_status: Mapped[str] = mapped_column(String(16), default="not_screened", index=True)
    screening_hits: Mapped[list] = mapped_column(JSON, default=list)
    screened_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    disposition_note: Mapped[str | None] = mapped_column(Text)

    # Set when a periodic refresh is due (risk-tier driven) or an ownership change occurs.
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    case: Mapped["OnboardingCase"] = relationship(back_populates="parties")


class BeneficialOwner(Base):
    """CDD Rule (31 CFR 1010.230) — 25%+ owners + control person for entity accounts.

    Superseded by OnboardingParty(role='beneficial_owner'); retained so existing rows can
    be backfilled without data loss. New writes go to OnboardingParty.
    """
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
