"""Who may take which decision on an agent's proposal.

Before this, every route that decides a recommendation used require_roles(*STAFF_ROLES),
which admits every role except a client. That was tolerable while approving a proposal
wrote a ledger entry and nothing else. It stopped being tolerable once approving a
rebalancing proposal started creating orders and settling them onto a client's book: a
research analyst or a branch leader could move client money.

The firm's rule:

  - Proposals that move money on the book are approved (or modified, or revised) only by
    an adviser or the portfolio team.
  - Compliance can veto them — dismiss before execution, or roll back afterwards — but
    cannot approve them. Stopping a trade and authorising one are different powers.
  - Platform administrators hold neither. Configuring the platform is not the same
    authority as instructing a client's portfolio, and require_roles' blanket admin
    override is deliberately not honoured here.
  - Every other proposal keeps its existing rule: any staff member.

Billing is the second thing that moves a client's money, so it gets the same shape of rule
with different people. Fees are issued and taken from cash only by operations or a branch
leader - not the adviser who earns them and not the portfolio team. Compliance may stop a
run or offset an issued one but not authorise it. Administrators hold neither.

This is the first slice of the per-role tool rights the conversation workspace needs (the
catalogue in docs/02_UX/agentic-ux-philosophy.md). It lives in one place so the gateway,
the routes and the browser can be made to read the same answer rather than each keeping
their own copy.
"""
from __future__ import annotations

from app.models.enums import AgentKey, HumanAction, UserRole

# Agents whose approved proposals create orders and settle them onto the book.
BOOK_MOVING_AGENTS: frozenset[str] = frozenset({AgentKey.DRIFT_REBALANCING.value})

TRADE_APPROVERS: frozenset[UserRole] = frozenset({UserRole.ADVISER, UserRole.PORTFOLIO_TEAM})
TRADE_VETO: frozenset[UserRole] = frozenset({UserRole.COMPLIANCE})

# Agents whose approved proposals issue fees and take them from client cash.
CASH_MOVING_AGENTS: frozenset[str] = frozenset({AgentKey.BILLING.value})
BILLING_APPROVERS: frozenset[UserRole] = frozenset({UserRole.OPERATIONS, UserRole.BRANCH_LEADER})
BILLING_VETO: frozenset[UserRole] = frozenset({UserRole.COMPLIANCE})

# Agents whose approved proposals record decisions on reconciliation breaks - escalating a
# difference to a custodian. No money moves, but the decision belongs to the people who can take a
# break to the custodian, the same people the reconciliation routes admit.
RECON_AGENTS: frozenset[str] = frozenset({AgentKey.RECONCILIATION.value})
RECON_DECIDERS: frozenset[UserRole] = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})

# Agents whose approved proposals accept a breach of a client's IPS. No money moves, but it sets aside
# the client's own terms, so it takes the people who may trade on them, and compliance keeps its veto.
IPS_AGENTS: frozenset[str] = frozenset({AgentKey.IPS_MONITORING.value})

STAFF: frozenset[UserRole] = frozenset(r for r in UserRole if r != UserRole.CLIENT)


class DecisionForbidden(Exception):
    """The role may not take this decision. Carries a reason fit to show the user."""


def moves_the_book(agent_key: str | AgentKey) -> bool:
    return str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in BOOK_MOVING_AGENTS


def moves_client_cash(agent_key: str | AgentKey) -> bool:
    return str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in CASH_MOVING_AGENTS


def _sets_aside_an_ips(agent_key: str | AgentKey) -> bool:
    return str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in IPS_AGENTS


def roles_for(agent_key: str | AgentKey, decision: str) -> frozenset[UserRole]:
    """The roles that may take `decision` on a proposal from `agent_key`.

    `decision` is a HumanAction value ("approve", "modify", "dismiss") or one of the
    non-HITL decisions "revise" and "rollback".
    """
    if moves_client_cash(agent_key):
        if decision in (HumanAction.APPROVE.value, HumanAction.MODIFY.value, "revise"):
            return BILLING_APPROVERS
        if decision in (HumanAction.DISMISS.value, "rollback"):
            return BILLING_APPROVERS | BILLING_VETO
        return frozenset()
    if str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in RECON_AGENTS:
        if decision in (HumanAction.APPROVE.value, HumanAction.MODIFY.value, HumanAction.DISMISS.value,
                        "revise", "rollback"):
            return RECON_DECIDERS
        return frozenset()
    if not moves_the_book(agent_key) and not _sets_aside_an_ips(agent_key):
        return STAFF
    if decision in (HumanAction.APPROVE.value, HumanAction.MODIFY.value, "revise"):
        return TRADE_APPROVERS
    if decision in (HumanAction.DISMISS.value, "rollback"):
        return TRADE_APPROVERS | TRADE_VETO
    # An unrecognised decision on a book-moving proposal is refused, not allowed through.
    return frozenset()


def queue_action(role: UserRole | str, agent_key: str | AgentKey) -> str | None:
    """What, if anything, a proposal from `agent_key` asks of `role` in its Needs-you queue.

    "decide" - the role may approve it. "veto" - the role's standing on it is the veto alone
    (dismiss before execution, roll back after): compliance on a proposal that moves a client's
    book or cash. None - nothing the role should be chased for.

    Compliance is deliberately not asked about proposals that move no money. It *may* decide them
    - roles_for says any staff member can - but being chased for every client-care draft made the
    queue noise; its queue is the proposals where its veto is the control. They remain on the
    Recommendations page and in read_recommendations."""
    role = UserRole(role) if not isinstance(role, UserRole) else role
    approve = role in roles_for(agent_key, "approve")
    if role in TRADE_VETO | BILLING_VETO:
        return "veto" if role in roles_for(agent_key, "dismiss") and not approve else None
    return "decide" if approve else None


def check(role: UserRole | str, agent_key: str | AgentKey, decision: str) -> None:
    """Raise DecisionForbidden unless `role` may take `decision` on this agent's proposal."""
    role = UserRole(role) if not isinstance(role, UserRole) else role
    allowed = roles_for(agent_key, decision)
    if role in allowed:
        return
    if moves_client_cash(agent_key):
        who = ("operations, a branch leader or compliance" if decision in ("dismiss", "rollback")
               else "operations or a branch leader")
        raise DecisionForbidden(
            f"This run issues fees and takes them from client cash, so only {who} may {decision} it."
        )
    if str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in RECON_AGENTS:
        raise DecisionForbidden(
            f"This proposal decides reconciliation breaks, so only operations or the portfolio team may {decision} it."
        )
    if _sets_aside_an_ips(agent_key):
        who = ("an adviser, the portfolio team or compliance" if decision in ("dismiss", "rollback")
               else "an adviser or the portfolio team")
        raise DecisionForbidden(f"This proposal accepts a breach of a client's IPS, so only {who} may {decision} it.")
    if moves_the_book(agent_key):
        if decision in ("dismiss", "rollback"):
            who = "an adviser, the portfolio team or compliance"
        else:
            who = "an adviser or the portfolio team"
        raise DecisionForbidden(
            f"This proposal moves money on a client's book, so only {who} may {decision} it."
        )
    raise DecisionForbidden(f"Your role may not {decision} this proposal.")
