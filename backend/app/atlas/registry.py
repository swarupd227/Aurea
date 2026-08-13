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
    from app.agents.estate_succession import EstateSucessionAgent
    from app.agents.meeting_companion import MeetingCompanionAgent
    from app.agents.behavioural_finance import BehaviouralFinanceAgent
    from app.agents.regulatory_countdown import RegulatoryCountdownAgent
    from app.agents.tax_intelligence import TaxIntelligenceAgent
    from app.agents.meeting_prep import MeetingPrepAgent
    from app.agents.next_best_action import NextBestActionAgent
    from app.agents.onboarding import OnboardingAgent
    from app.agents.research_reporting import ResearchReportingAgent
    # L100 additions
    from app.agents.ips_drafting import IPSDraftingAgent
    from app.agents.asset_location import AssetLocationAgent
    from app.agents.wallet_share_scout import WalletShareScoutAgent
    from app.agents.glide_path import GlidePathAgent
    from app.agents.reverse_churning import ReverseChurningAgent
    # L200 additions
    from app.agents.adverse_media_pep import AdverseMediaPEPScreenerAgent
    from app.agents.edd_sow_narrator import EDDSOWNarratorAgent
    from app.agents.rollover_pte_documenter import RolloverPTEDocumenterAgent
    from app.agents.nigo_prevention import NIGOPreventionAgent
    from app.agents.abandonment_recovery import AbandonmentRecoveryAgent
    # Phase 3 mock integrations
    from app.agents.cip_identity_verifier import CIPIdentityVerifierAgent
    from app.agents.custodian_account_opener import CustodianAccountOpenerAgent

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
        EstateSucessionAgent(),
        TaxIntelligenceAgent(),
        BehaviouralFinanceAgent(),
        RegulatoryCountdownAgent(),
        # L100 additions
        IPSDraftingAgent(),
        AssetLocationAgent(),
        WalletShareScoutAgent(),
        GlidePathAgent(),
        ReverseChurningAgent(),
        # L200 additions
        AdverseMediaPEPScreenerAgent(),
        EDDSOWNarratorAgent(),
        RolloverPTEDocumenterAgent(),
        NIGOPreventionAgent(),
        AbandonmentRecoveryAgent(),
        # Phase 3 mock integrations
        CIPIdentityVerifierAgent(),
        CustodianAccountOpenerAgent(),
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
