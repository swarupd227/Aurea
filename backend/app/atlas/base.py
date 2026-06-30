"""AgentBase — every Aurea agent follows a sense → think → act → learn loop at a defined
autonomy tier with an explicit human checkpoint (spec §7)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AgentKey, AutonomyTier
from app.models.governance import AgentRun
from app.models.tenant import Firm


@dataclass
class Subject:
    """What a run is about (a household / mandate / person)."""

    type: str | None = None
    id: uuid.UUID | None = None
    label: str | None = None


@dataclass
class RecommendationDraft:
    title: str
    summary: str
    rationale: str = ""
    confidence: float = 0.7
    priority: int = 3
    subject: Subject = field(default_factory=Subject)
    payload: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    citations: list = field(default_factory=list)


@dataclass
class AgentContext:
    session: AsyncSession
    firm: Firm
    run: AgentRun
    tier: AutonomyTier
    guardrails: dict
    config: dict  # per-firm agent config
    subject: Subject


class BaseAgent:
    """Subclasses implement sense/think; act has a safe default (no live external effect)."""

    key: AgentKey
    name: str
    lifecycle_stage: str
    default_tier: AutonomyTier = AutonomyTier.TIER_1
    # Whether this agent runs on a schedule (monitoring) vs on demand.
    scheduled: bool = False

    async def sense(self, ctx: AgentContext) -> dict:
        """Gather signals from the client brain. Returns a JSON-able 'sensed' state."""
        return {}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        """Reason over sensed state and produce zero or more recommendation drafts."""
        return []

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Execute an APPROVED recommendation. Default: record intent only (no live effect).

        The drift agent overrides this to route a draft order set to the (mock) OMS."""
        return {"executed": False, "note": "No external action implemented for this agent."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        """Reverse the effect of a previously executed recommendation (spec §10.2 — rollback is
        first-class). Default: nothing to reverse. Agents that mutate the brain override this."""
        return {"reversed": False, "note": "This action has no reversible effect."}
