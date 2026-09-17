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

STAFF: frozenset[UserRole] = frozenset(r for r in UserRole if r != UserRole.CLIENT)


class DecisionForbidden(Exception):
    """The role may not take this decision. Carries a reason fit to show the user."""


def moves_the_book(agent_key: str | AgentKey) -> bool:
    return str(agent_key.value if isinstance(agent_key, AgentKey) else agent_key) in BOOK_MOVING_AGENTS


def roles_for(agent_key: str | AgentKey, decision: str) -> frozenset[UserRole]:
    """The roles that may take `decision` on a proposal from `agent_key`.

    `decision` is a HumanAction value ("approve", "modify", "dismiss") or one of the
    non-HITL decisions "revise" and "rollback".
    """
    if not moves_the_book(agent_key):
        return STAFF
    if decision in (HumanAction.APPROVE.value, HumanAction.MODIFY.value, "revise"):
        return TRADE_APPROVERS
    if decision in (HumanAction.DISMISS.value, "rollback"):
        return TRADE_APPROVERS | TRADE_VETO
    # An unrecognised decision on a book-moving proposal is refused, not allowed through.
    return frozenset()


def check(role: UserRole | str, agent_key: str | AgentKey, decision: str) -> None:
    """Raise DecisionForbidden unless `role` may take `decision` on this agent's proposal."""
    role = UserRole(role) if not isinstance(role, UserRole) else role
    allowed = roles_for(agent_key, decision)
    if role in allowed:
        return
    if moves_the_book(agent_key):
        if decision in ("dismiss", "rollback"):
            who = "an adviser, the portfolio team or compliance"
        else:
            who = "an adviser or the portfolio team"
        raise DecisionForbidden(
            f"This proposal moves money on a client's book, so only {who} may {decision} it."
        )
    raise DecisionForbidden(f"Your role may not {decision} this proposal.")
