"""Autonomy policy engine (spec §10.2).

Resolves the autonomy tier and guardrails for an (agent × mandate) pair, honouring the most
specific policy a firm has configured, and enforces the human checkpoint required at each
tier. Also exposes the kill-switch check (an agent paused by config or by surveillance)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AgentKey, AutonomyTier, MandateType
from app.models.tenant import AgentConfig, AutonomyPolicy


async def resolve_tier(
    session: AsyncSession,
    firm_id: uuid.UUID,
    agent_key: AgentKey,
    mandate_type: MandateType | None,
) -> tuple[AutonomyTier, dict]:
    """Return (tier, guardrails). Mandate-specific policy beats the agent default beats config."""
    policies = (
        await session.execute(
            select(AutonomyPolicy).where(
                AutonomyPolicy.firm_id == firm_id, AutonomyPolicy.agent_key == agent_key
            )
        )
    ).scalars().all()

    specific = next((p for p in policies if p.mandate_type == mandate_type), None)
    default = next((p for p in policies if p.mandate_type is None), None)
    chosen = specific or default
    if chosen:
        return chosen.tier, dict(chosen.guardrails or {})

    cfg = (
        await session.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == firm_id, AgentConfig.agent_key == agent_key
            )
        )
    ).scalar_one_or_none()
    return (cfg.default_tier if cfg else AutonomyTier.TIER_1), {}


async def agent_paused(session: AsyncSession, firm_id: uuid.UUID, agent_key: AgentKey) -> tuple[bool, str | None]:
    """Kill-switch check: is this agent paused (by config or surveillance)?"""
    cfg = (
        await session.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == firm_id, AgentConfig.agent_key == agent_key
            )
        )
    ).scalar_one_or_none()
    if cfg and cfg.paused:
        return True, cfg.paused_reason or "Agent paused by policy."
    if cfg and not cfg.enabled:
        return True, "Agent disabled for this firm."
    return False, None


def requires_human_checkpoint(tier: AutonomyTier) -> bool:
    """Tiers 1 & 2 always route to a human before effect; Tier 3 is bounded post-hoc review."""
    return tier in (AutonomyTier.TIER_1, AutonomyTier.TIER_2)
