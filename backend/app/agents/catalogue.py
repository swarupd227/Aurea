"""Static catalogue metadata for the agent roster (spec §7.2, App. B) — used by the UI."""
from __future__ import annotations

from app.models.enums import AgentKey, AutonomyTier

CATALOGUE: dict[str, dict] = {
    AgentKey.ONBOARDING_KYC_AML: {
        "name": "Onboarding · KYC · AML", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "New prospect or referral; intake form; document upload.",
        "acts": "Enriches the prospect, verifies documents, runs AML/CFT screening, drafts "
                "suitability & mandate set-up, surfaces exceptions.",
        "checkpoint": "Compliance signs off; escalates ambiguity rather than deciding it.",
    },
    AgentKey.BOOK_INTEGRATION: {
        "name": "Book Integration", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "book_batch",
        "senses": "Acquisition of an advisory book; new data feed.",
        "acts": "Reconciles and maps client, account and holding data into the client brain; flags conflicts.",
        "checkpoint": "Operations validates mappings before they become golden records.",
    },
    AgentKey.MEETING_PREP: {
        "name": "Meeting Preparation", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Upcoming meeting in calendar/CRM.",
        "acts": "Assembles a prepared brief: portfolio, performance, plan changes, life events, house views, topics.",
        "checkpoint": "Adviser owns the conversation; brief is a starting point.",
    },
    AgentKey.MEETING_COMPANION: {
        "name": "Meeting Companion & Note", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Live or recorded client meeting.",
        "acts": "Captures notes, drafts follow-ups, updates CRM, proposes tasks.",
        "checkpoint": "Adviser approves notes and any actions before they take effect.",
    },
    AgentKey.RESEARCH_REPORTING: {
        "name": "Research & Reporting", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Reporting cycle; ad-hoc request; market event.",
        "acts": "Drafts client-ready performance summaries and commentary grounded in firm research.",
        "checkpoint": "Adviser/research reviews before anything is client-facing.",
    },
    AgentKey.DRIFT_REBALANCING: {
        "name": "Drift & Tax-Managed Rebalancing", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_2, "subject": "mandate", "lighthouse": True,
        "senses": "Continuous monitoring of public and private holdings vs target.",
        "acts": "Runs whole-portfolio, tax-aware optimisation (lot selection, CGT budgets, loss "
                "harvesting); drafts plain-language rationale and a multi-custodian order set.",
        "checkpoint": "Adviser approves / modifies / dismisses before anything reaches the OMS.",
    },
    AgentKey.NEXT_BEST_ACTION: {
        "name": "Next-Best-Action & Growth", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Life-stage, tax, market, anomaly and relationship signals.",
        "acts": "Pushes prioritised, explainable opportunities, risks and anomalies to the adviser.",
        "checkpoint": "Adviser chooses whether and how to act.",
    },
    AgentKey.CLIENT_CARE: {
        "name": "Client Care & Retention", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Volatility; milestones; heir engagement gaps; at-risk signals.",
        "acts": "Proposes proactive, personalised outreach — stress-grounded during market stress.",
        "checkpoint": "Adviser approves outreach; Tier 3 only for pre-approved, low-risk comms.",
    },
    AgentKey.CONDUCT_SURVEILLANCE: {
        "name": "Conduct Surveillance", "stage": "Protect & govern",
        "default_tier": AutonomyTier.TIER_2, "subject": "firm",
        "senses": "Every recommendation and client communication generated on the platform.",
        "acts": "Monitors for suitability, conduct and fair-treatment risk; supervises other agents; "
                "writes to the decision ledger.",
        "checkpoint": "Flags route to compliance; can pause an agent's autonomy automatically.",
    },
}
