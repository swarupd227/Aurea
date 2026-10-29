"""Which agents a conversation may start, who may start them, and what a run needs.

Starting a run is a third thing, separate from the other two controls (CLAUDE.md rule 4):

  - confirmation   - "did you mean this": the Confirm / Not now card in the thread;
  - decision rights - "may this role decide what the agent proposes": decision_rights.py;
  - autonomy tier  - "may the agent act on its own": the firm's policy, resolved per mandate.

This file answers only the first question's inputs - which agents can be named from a sentence,
which roles may name them, and what subject each needs. It never grants a decision, and it never
reads or changes a tier. A run started here is held to a proposal when it would move a client's
book or cash, whatever the tier says (see `forced_to_proposal`).
"""
from __future__ import annotations

from app.core import decision_rights
from app.models.enums import AgentKey, AutonomyTier, UserRole

R = UserRole

# Agents a sentence cannot start, and why. Each is page-first today: it needs an inbound book batch
# or a recorded meeting that a sentence cannot name, or it has its own tool.
NOT_FROM_CONVERSATION: dict[AgentKey, str] = {
    AgentKey.ABANDONMENT_RECOVERY: "it belongs to the onboarding desk - run it from the Onboarding page",
    AgentKey.BOOK_INTEGRATION: "it works on an inbound book batch - run it from Book integration",
    AgentKey.MEETING_COMPANION: "it works on a recorded meeting - run it from Meetings",
    AgentKey.BILLING: "billing has its own tool - ask to prepare a billing run",
}

# Who may start an agent. Everyone below holds the Cockpit's Delegate bar today; the compliance
# and research agents are added for the people whose job they are. Administrators configure the
# platform and do not instruct portfolios, so they are not here.
_DEFAULT_RUN_ROLES: frozenset[UserRole] = frozenset({R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM, R.BRANCH_LEADER})
_ALSO_RUN_ROLES: dict[AgentKey, frozenset[UserRole]] = {
    AgentKey.RESEARCH_REPORTING: frozenset({R.RESEARCH_CIO}),
}
# The agents that work on one onboarding case, named by the case's id (read_onboarding_cases).
CASE_AGENTS: frozenset[AgentKey] = frozenset({
    AgentKey.ONBOARDING_KYC_AML, AgentKey.NIGO_PREVENTION, AgentKey.ADVERSE_MEDIA_PEP,
    AgentKey.EDD_SOW_NARRATOR, AgentKey.ROLLOVER_PTE_DOCUMENTER, AgentKey.CIP_IDENTITY_VERIFIER,
    AgentKey.CUSTODIAN_ACCOUNT_OPENER,
})
# Supervision is compliance's job, not the advice desk's: these replace the default. A case agent
# is started by the people the Onboarding page is for.
_ONLY_RUN_ROLES: dict[AgentKey, frozenset[UserRole]] = {
    AgentKey.CONDUCT_SURVEILLANCE: frozenset({R.COMPLIANCE}),
    AgentKey.REVERSE_CHURNING: frozenset({R.COMPLIANCE}),
    **{k: frozenset({R.ADVISER, R.OPERATIONS, R.COMPLIANCE}) for k in CASE_AGENTS},
}

# What an agent needs to be pointed at. Anything not listed here may run firm-wide, which is
# what the Delegate bar does when no client is named.
SUBJECT_REQUIRED: dict[AgentKey, str] = {
    AgentKey.DRIFT_REBALANCING: "mandate",
    AgentKey.RESEARCH_REPORTING: "household",
    AgentKey.MEETING_PREP: "household",
    **{k: "onboarding_case" for k in CASE_AGENTS},
}

TIER_LABEL: dict[str, str] = {
    AutonomyTier.TIER_1.value: "Tier 1 · assistive",
    AutonomyTier.TIER_2.value: "Tier 2 · supervised",
    AutonomyTier.TIER_3.value: "Tier 3 · bounded autonomy",
}


def _key(agent_key: str | AgentKey) -> AgentKey:
    return agent_key if isinstance(agent_key, AgentKey) else AgentKey(str(agent_key))


def run_roles(agent_key: str | AgentKey) -> frozenset[UserRole]:
    """The roles that may start this agent from a conversation (empty if it cannot be)."""
    key = _key(agent_key)
    if key in NOT_FROM_CONVERSATION:
        return frozenset()
    if key in _ONLY_RUN_ROLES:
        return _ONLY_RUN_ROLES[key]
    return _DEFAULT_RUN_ROLES | _ALSO_RUN_ROLES.get(key, frozenset())


def may_run(role: UserRole | str, agent_key: str | AgentKey) -> bool:
    return UserRole(role) in run_roles(agent_key)


def runnable_agents(role: UserRole | str) -> list[AgentKey]:
    """Every agent this role may start from a conversation, in catalogue order."""
    from app.agents.catalogue import CATALOGUE

    role = UserRole(role)
    return [k for k in (_key(k) for k in CATALOGUE) if role in run_roles(k)]


# The tool's roles are everyone who may start at least one agent.
ANY_RUN_ROLES: frozenset[UserRole] = frozenset(
    r for k in AgentKey for r in run_roles(k)
)


def conversation_agent_keys() -> list[str]:
    """Agent keys the run_agent tool accepts, for its description."""
    from app.agents.catalogue import CATALOGUE

    return [_key(k).value for k in CATALOGUE if _key(k) not in NOT_FROM_CONVERSATION]


def forced_to_proposal(agent_key: str | AgentKey) -> bool:
    """A run started from a conversation by an agent that moves a client's book or cash stops
    at a proposal, whatever its autonomy tier. A tier decides what an agent may do on a schedule
    or on a page; it is not a licence to trade or take fees because a sentence was confirmed.
    The decision to approve stays with decision_rights - this only keeps the agent from
    approving for itself."""
    return decision_rights.moves_the_book(agent_key) or decision_rights.moves_client_cash(agent_key)


def acts_on_its_own(agent_key: str | AgentKey, tier: str) -> bool:
    """Whether a run at this tier would carry out what it proposes without a human decision."""
    return str(tier) == AutonomyTier.TIER_3.value and not forced_to_proposal(agent_key)


def deciders(agent_key: str | AgentKey) -> list[str]:
    """Role names that may approve what this agent proposes (decision_rights, not tiers)."""
    return sorted(r.value for r in decision_rights.roles_for(agent_key, "approve"))
