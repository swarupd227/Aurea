"""AI / LLM model-risk management (L200-8 §8) — the one piece of the platform's own
agentic-AI reference-architecture alignment that was missing: a use-case inventory with
owners and risk tiers, a change-control log for prompts/models/tools, and production
monitoring for entitlement violations at zero tolerance.

Everything else L200-8 §8 asks for already exists elsewhere and is deliberately reused,
not duplicated: golden-set evaluation (`app.provenance.eval_gates`), per-agent quality
scoring and auto-narrowing autonomy (`app.provenance.evaluation`), and usage/cost
telemetry (`app.llm.usage`). This module is the register that ties a use case's identity
(who owns it, what risk tier it's rated) to those existing signals, plus the two concepts
that had no home at all: change control and entitlement-violation logging.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

RISK_TIERS = ("low", "medium", "high")
USE_CASE_CATEGORIES = ("agent", "tool", "narration")
USE_CASE_STATUSES = ("active", "retired")
CHANGE_TYPES = ("prompt", "model", "tool_definition", "risk_tier", "other")


class AIUseCase(Base):
    """One AI-touching capability — an agent, a conversational tool, or a narration
    point — with a named owner and a risk tier, the register L200-8 §8 calls for."""

    __tablename__ = "ai_use_case"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    use_case_key: Mapped[str] = mapped_column(String(64), index=True)  # agent_key, tool_key, or a narration label
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(16), default="agent")  # agent | tool | narration
    description: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(120))
    risk_tier: Mapped[str] = mapped_column(String(8), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    review_cadence_days: Mapped[int] = mapped_column(Integer, default=180)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIChangeLogEntry(Base):
    """A change-control record for a prompt, model choice, or tool definition — treating
    the AI layer's own source "as on code" per L200-8 §8, i.e. reviewed and logged, not
    edited silently. This is a paper trail an admin records, not an automatic source-diff
    tracker — there is no in-app prompt/tool editor today for this to attach to."""

    __tablename__ = "ai_change_log_entry"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    use_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_use_case.id", ondelete="SET NULL"), nullable=True, index=True
    )
    change_type: Mapped[str] = mapped_column(String(16), default="other")
    description: Mapped[str] = mapped_column(Text)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EntitlementViolation(Base):
    """A blocked attempt to act outside what a role/agent was entitled to — the
    zero-tolerance production-monitoring metric L200-8 §8 names. Every row here is an
    attempt the existing RBAC/decision-rights layers already stopped; this table exists so
    "how many, by whom, how often" is a query instead of unlogged noise."""

    __tablename__ = "entitlement_violation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    actor_role: Mapped[str] = mapped_column(String(32))
    violation_type: Mapped[str] = mapped_column(String(32))  # tool_role_forbidden | decision_rights_forbidden
    subject_key: Mapped[str] = mapped_column(String(64))     # the tool_key or agent_key involved
    detail: Mapped[str] = mapped_column(Text)
    blocked: Mapped[bool] = mapped_column(Boolean, default=True)  # always True today — nothing gets through
