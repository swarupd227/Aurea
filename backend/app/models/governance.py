"""Provenance — the governance spine (spec §10): agent runs, recommendations,
the immutable hash-chained decision ledger, and conduct-surveillance flags."""
from __future__ import annotations

import uuid
from datetime import datetime

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import (
    AgentKey,
    AgentRunStatus,
    AutonomyTier,
    RecommendationStatus,
    SurveillanceSeverity,
)


class AgentRun(Base):
    """One execution of an agent's sense→think→act→learn loop."""

    __tablename__ = "agent_run"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[AgentKey] = mapped_column(String(48), index=True)
    status: Mapped[AgentRunStatus] = mapped_column(String(24), default=AgentRunStatus.PENDING)
    tier: Mapped[AutonomyTier] = mapped_column(String(16), default=AutonomyTier.TIER_1)
    trigger: Mapped[str] = mapped_column(String(120), default="manual")
    # Subject of the run (a household / mandate / person), for filtering in Studio.
    subject_type: Mapped[str | None] = mapped_column(String(24))
    subject_id: Mapped[uuid.UUID | None] = mapped_column()
    subject_label: Mapped[str | None] = mapped_column(String(200))
    # Working state captured across the loop phases (sense outputs, reasoning trace).
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Recommendation(Base):
    """An agent's proposed action, surfaced to a human at the HITL gate (spec Table 13)."""

    __tablename__ = "recommendation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), index=True
    )
    agent_key: Mapped[AgentKey] = mapped_column(String(48), index=True)
    tier: Mapped[AutonomyTier] = mapped_column(String(16))
    status: Mapped[RecommendationStatus] = mapped_column(
        String(16), default=RecommendationStatus.PROPOSED, index=True
    )
    title: Mapped[str] = mapped_column(String(280))
    summary: Mapped[str] = mapped_column(Text)
    # Plain-language rationale (LLM-authored, grounded in firm research).
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(default=0.0)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 (highest) .. 5
    subject_type: Mapped[str | None] = mapped_column(String(24))
    subject_id: Mapped[uuid.UUID | None] = mapped_column()
    subject_label: Mapped[str | None] = mapped_column(String(200))
    # Structured payload: e.g. the proposed order set, the outreach draft, the brief.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Data the recommendation used, with lineage + confidence (for the ledger).
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    # Firm research / house views cited.
    citations: Mapped[list] = mapped_column(JSON, default=list)

    # Human action record.
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    modified_payload: Mapped[dict | None] = mapped_column(JSON)

    run: Mapped["AgentRun"] = relationship(back_populates="recommendations")


class LedgerEntry(Base):
    """An append-only, hash-chained decision-ledger entry (spec §10.1).

    Tamper-evidence: each entry stores the hash of the previous entry plus a hash of its
    own canonical content. Any retroactive edit breaks the chain (verified by Provenance)."""

    __tablename__ = "ledger_entry"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    # Monotonic per-firm sequence number.
    seq: Mapped[int] = mapped_column(Integer, index=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), index=True)

    event_type: Mapped[str] = mapped_column(String(48))  # recommendation/decision/surveillance/...
    agent_key: Mapped[str | None] = mapped_column(String(48))
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column()
    actor: Mapped[str | None] = mapped_column(String(200))  # human or 'agent'
    # The full canonical record: trigger, data+lineage+confidence, research cited,
    # recommendation + rationale, tier, human action.
    content: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentEvaluation(Base):
    """A point-in-time quality score for an agent, from the evaluation harness (spec §10.2/§13).

    Agents are continuously evaluated against outcomes; a quality regression narrows autonomy
    automatically."""

    __tablename__ = "agent_evaluation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[AgentKey] = mapped_column(String(48), index=True)
    quality_score: Mapped[float] = mapped_column(default=0.0)  # 0..1
    grade: Mapped[str] = mapped_column(String(16), default="unrated")  # healthy/watch/regressed
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)


class AutonomyChange(Base):
    """An audit record of an autonomy-tier change (manual or adaptive)."""

    __tablename__ = "autonomy_change"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[AgentKey] = mapped_column(String(48), index=True)
    from_tier: Mapped[str | None] = mapped_column(String(16))
    to_tier: Mapped[str | None] = mapped_column(String(16))
    paused: Mapped[bool] = mapped_column(default=False)
    automatic: Mapped[bool] = mapped_column(default=True)
    reason: Mapped[str] = mapped_column(Text)


class AgentActivity(Base):
    """A live activity event from the agentic workforce — drives the 'pulse' / activity stream.

    Deliberately lightweight and append-only; it's a display timeline, distinct from the
    governance-grade decision ledger."""

    __tablename__ = "agent_activity"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # ActivityKind
    summary: Mapped[str] = mapped_column(Text)
    subject_label: Mapped[str | None] = mapped_column(String(200))
    autonomous: Mapped[bool] = mapped_column(default=False)  # Tier-3 acted-on-its-own
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class SurveillanceFlag(Base):
    """A conduct-surveillance finding over a recommendation/communication (spec §10.2)."""

    __tablename__ = "surveillance_flag"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_agent_key: Mapped[str | None] = mapped_column(String(48))
    severity: Mapped[SurveillanceSeverity] = mapped_column(String(12), default=SurveillanceSeverity.INFO)
    category: Mapped[str] = mapped_column(String(64))  # suitability/conduct/fair_treatment/...
    finding: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(default=False)
    auto_paused_agent: Mapped[bool] = mapped_column(default=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated: Mapped[bool] = mapped_column(default=False)
    escalated_to: Mapped[str | None] = mapped_column(String(200), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wave I: holding alert support
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
