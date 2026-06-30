"""Tenant (firm) and firm-wide configuration.

This is the backbone of Aurea-as-a-platform: almost everything a firm would want to
brand, enable, or govern lives in JSONB config here or in related config tables, so the
product is generic rather than wired to one client (build guideline #1)."""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import AgentKey, AutonomyTier, MandateType


class Firm(Base):
    """A tenant. The platform supports many; the demo seeds one."""

    __tablename__ = "firm"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    # Jurisdiction drives regulatory framing (spec §10 — FMA/NZ default).
    jurisdiction: Mapped[str] = mapped_column(String(8), default="NZ")
    regulator: Mapped[str | None] = mapped_column(String(120), default="FMA")
    base_currency: Mapped[str] = mapped_column(String(3), default="NZD")

    # Branding for Studio + Canvas white-labelling.
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    # e.g. {"primary":"#1f3a5f","accent":"#c9a227","logo_text":"...","tagline":"..."}

    # Free-form firm-wide settings (data residency note, AI usage policy text, etc.).
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    # Per-task model selection (overrides global defaults). {"advice":"claude-opus-4-8",...}
    model_config_json: Mapped[dict] = mapped_column("model_config", JSON, default=dict)

    # LLM provider credentials, set in-app by an admin (write-only; redacted on read).
    # {"anthropic_api_key": "...", "openai_api_key": "..."}
    llm_config: Mapped[dict] = mapped_column(JSON, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    agent_configs: Mapped[list["AgentConfig"]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )
    autonomy_policies: Mapped[list["AutonomyPolicy"]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )


class AgentConfig(Base):
    """Per-firm enablement and default tier for each agent in the catalogue (spec App. B)."""

    __tablename__ = "agent_config"
    __table_args__ = (UniqueConstraint("firm_id", "agent_key", name="uq_firm_agent"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"))
    agent_key: Mapped[AgentKey] = mapped_column(String(48))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_tier: Mapped[AutonomyTier] = mapped_column(String(16), default=AutonomyTier.TIER_1)
    # Kill-switch: when paused the runtime refuses to start runs for this agent.
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    # Scheduler: cron expression (e.g. "0 7 * * 1") and enable flag.
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Agent-specific tunables (thresholds, cadence, prompt overrides, model override).
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    firm: Mapped["Firm"] = relationship(back_populates="agent_configs")


class AutonomyPolicy(Base):
    """Binds (agent × mandate-type) → tier + guardrails (spec §10.2).

    The most specific matching policy wins; a row with mandate_type=NULL is the default
    for that agent across all mandates."""

    __tablename__ = "autonomy_policy"
    __table_args__ = (
        UniqueConstraint("firm_id", "agent_key", "mandate_type", name="uq_policy_scope"),
    )

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"))
    agent_key: Mapped[AgentKey] = mapped_column(String(48))
    mandate_type: Mapped[MandateType | None] = mapped_column(String(24), nullable=True)
    tier: Mapped[AutonomyTier] = mapped_column(String(16), default=AutonomyTier.TIER_1)
    # Guardrails: max trade value, requires_compliance, blocked actions, etc.
    guardrails: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)

    firm: Mapped["Firm"] = relationship(back_populates="autonomy_policies")


class FirmSegment(Base):
    """Configurable client segments with fee structure (spec §4 — generic platform config)."""

    __tablename__ = "firm_segment"
    __table_args__ = (UniqueConstraint("firm_id", "slug", name="uq_firm_segment"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(120))
    fee_tier_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_aum_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MandateTypeConfig(Base):
    """Per-firm mandate type labels and default autonomy tier."""

    __tablename__ = "mandate_type_config"
    __table_args__ = (UniqueConstraint("firm_id", "slug", name="uq_firm_mandate_type"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(120))
    default_autonomy_tier: Mapped[AutonomyTier] = mapped_column(String(16), default=AutonomyTier.TIER_2)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationConfig(Base):
    """Per-firm notification rules: which events trigger which channels."""

    __tablename__ = "notification_config"
    __table_args__ = (UniqueConstraint("firm_id", "event_type", "channel", name="uq_notif_config"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(32))   # email | in_app | webhook
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list)   # email addrs or role names
    config: Mapped[dict] = mapped_column(JSON, default=dict)       # throttle_minutes, digest_time, etc.
