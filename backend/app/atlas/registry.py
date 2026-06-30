"""Maps agent keys to their implementations. Importing builds the catalogue."""
from __future__ import annotations

from app.atlas.base import BaseAgent
from app.models.enums import AgentKey


def build_registry() -> dict[AgentKey, BaseAgent]:
    # Imported here to avoid circulars at module import time.
    from app.agents.book_integration import BookIntegrationAgent
    from app.agents.client_care import ClientCareAgent
    from app.agents.conduct_surveillance import ConductSurveillanceAgent
    from app.agents.drift_rebalancing import DriftRebalancingAgent
    from app.agents.meeting_companion import MeetingCompanionAgent
    from app.agents.meeting_prep import MeetingPrepAgent
    from app.agents.next_best_action import NextBestActionAgent
    from app.agents.onboarding import OnboardingAgent
    from app.agents.research_reporting import ResearchReportingAgent

    agents: list[BaseAgent] = [
        OnboardingAgent(),
        BookIntegrationAgent(),
        MeetingPrepAgent(),
        MeetingCompanionAgent(),
        ResearchReportingAgent(),
        DriftRebalancingAgent(),
        NextBestActionAgent(),
        ClientCareAgent(),
        ConductSurveillanceAgent(),
    ]
    return {a.key: a for a in agents}


_REGISTRY: dict[AgentKey, BaseAgent] | None = None


def get_agent(key: AgentKey) -> BaseAgent | None:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY.get(key)


def all_agents() -> dict[AgentKey, BaseAgent]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY
